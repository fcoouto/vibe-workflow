# Design revision: admission-bound account continuity

**Status:** Approved by the user for implementation after independent PASS of D1–D5.
**Source baseline:** `e791ec02b8a570fdff27abd80d657a78dd7059d4`.
This is the approved written revision, now retained in the repository. Approval
does not authorize installation, account access, live calls or activation.
It refines account-continuity ownership in the
[provider-managed design](2026-11-15-smart-router-provider-managed-design.md);
unchanged requirements remain binding.

## 1. Purpose and approach

Keep quota-optional routing, included-capacity accounting and the existing attempt lifecycle. Move account continuity into the shared permit boundary.

Every subscription admission— included or provider-managed fallback—must retain evidence of the account identity used for its decision. Every dispatch must verify that evidence before issuing a permit.

Use existing producer state files, admission records and dispatch gates. Add no account registry, service, polling or measurement call.

Rejected approaches:

- **Adapter-only checks:** cannot reliably cover separate preparation, consumption and continuation calls.
- **Timestamp comparisons:** miss same-second account changes.
- **Identity-derived pool names:** risk changing genuine sharing and historical accounting.

## 2. Account identity and change generation

Each native producer retains:

- The existing derived account key.
- An opaque, randomly generated 128-bit **generation**.
- A coherent association between that generation and its published account state.

The generation records observed continuity. It is not a provider UUID or proof of backend identity.

| Event | Required behavior |
|---|---|
| Observed A→B | Generate a different generation. |
| Observed A→B→A | Generate again; the final A must not match the original A’s generation. |
| Same-account refresh | Preserve generation only when prior state proves continuity. |
| Explicit logout or account refusal | Invalidate continuity; the next successful acquisition establishes a fresh generation. |
| Missing, corrupt, legacy or re-created state | Return no eligible fact; establish a fresh generation through successful acquisition. |
| Restart | Preserve coherent persisted state; do not rotate merely because a process restarted. |
| Transient acquisition failure | Preserve only previously proven continuity; incomplete or stale metadata supplies no eligibility. |

### Provenance before refresh clearing

Validate the **pre-existing state before writing any refresh-pending record**.

A pending record may preserve a generation only when the previous complete or pending state coherently carried that generation. Legacy `account=None`, missing generations or inconsistent state cannot become trusted merely because new code writes a pending record.

This prevents an old writer’s B-success/clear sequence from reviving an earlier A generation during re-upgrade.

### Publication and catalog coherence

Publish metadata in this order under existing producer ownership and locks:

1. Establish or invalidate continuity evidence.
2. Store the matching catalog.
3. Publish the complete account record carrying that generation.
4. Confirm optional quota readings only after their complete, coherent storage.

Pending or inconsistent state returns neither an eligible billing fact nor a usable native binding. A same-generation refresh may retain earlier complete metadata only while it remains valid.

Generation must participate in the native connection identity as well as the billing marker. Use the existing opaque connection-binding value; do not add keys to the catalog’s exact schema. This lets retained native checks distinguish ABA generations and rejects older-generation catalogs.

These ordering rules do not claim a transaction spanning producer files, catalogs, windows and SQLite. Crash and durability behavior require later proof.

## 3. Admission and dispatch contract

Retain a domain-separated digest of:

- Code-selected producer identity and generation.
- Exact path.
- Logical account scope.
- Pool.

Store this subscription marker in the existing `binding.credential` slot. The immutable billing class determines its interpretation. Funded-route credential semantics remain unchanged.

Do **not** include observation timestamps, expiry times or overflow settings in identity.

The collector must retain the verified fact rather than discard it after a boolean check. Eligibility, included readings and the persisted marker must refer to the same identity generation. A detected mismatch excludes the route; fallback cannot bypass it.

The shared in-transaction dispatch gate must refuse issuance when:

- The admission lacks required binding evidence.
- Current required metadata is missing, expired or unreadable.
- Current identity or generation differs from the admission marker.

The gate covers both subscription admission bases and every supported permit consumer. It performs bounded local reads only—never acquisition.

Existing catalog, explicit-choice, authority, funding and exposure checks remain required.

## 4. Continuation roots

Delegation owns the trusted relationship between the root assignment and its continuation attempts.

Before preparing a continuation:

1. Resolve the original admission through existing trusted linkage.
2. Require a valid marked subscription root and its original route.
3. Preserve the existing ended-turn and successful-close requirements.
4. Require the continuation’s admission marker to equal the root marker.

Recheck the root relationship when consuming a prepared continuation. The central dispatch gate independently verifies current account continuity.

A missing, pruned, legacy or incompatible root refuses continuation. **Never fall back to the original selection, reconstruct identity from current login, or substitute another route.**

A refused, newly admitted but provably unsent continuation follows existing closure handling. Already-issued or uncertain permits retain their existing recovery rules.

## 5. Legacy permits and historical records

No identity backfill or historical record rewriting is permitted.

| Record | Required treatment |
|---|---|
| No binding | Refuse new dispatch through the existing missing-binding rule. |
| Subscription binding with no account marker | Refuse unsent dispatch as missing account binding. |
| Valid marked subscription admission | Dispatch only after current-marker verification. |
| Dispatched or uncertain admission | Preserve exposure, duplicate protection, settlement and reconciliation. |
| Terminal admission | Preserve its original meaning and history. |
| Legacy or unavailable continuation root | Refuse continuation; do not manufacture a replacement root. |
| Funded admission | Preserve existing credential and monetary checks. |

Identity verification governs **permit issuance**, not permission to settle existing exposure. An account switch must not prevent legitimate completion or reconciliation.

Historical strict replay must retain its original selection and accounting interpretation. New identity checks must not retroactively turn old admissions into fallback or assert that historical identity verification occurred.

## 6. Upgrade, rollback and mixed versions

### Upgrade

Under separately authorized deployment:

- Quiesce incompatible producers, dispatchers and launchers.
- Preserve ledger records and uncertain exposure.
- Establish coherent new-format producer state through owned acquisition.
- Refuse legacy unsent permits rather than upgrading their authority.

No manual cache deletion or fabricated identity migration is required.

### Rollback

Rollback below the continuity-check revision is **unsupported by default**.

It becomes supportable only when the exact target revision demonstrably preserves the invariant or blocks **every affected dispatch path**. Absence of currently managed projects or qualification evidence is not sufficient without that proof.

An old dispatcher rejecting a new marked admission is useful protection, but does not make rollback safe: an old writer and old dispatcher can still create and dispatch unprotected admissions.

If safe blocking cannot be established, fix forward.

### Mixed versions and re-upgrade

Do not claim general mixed-version safety.

New dispatchers refuse legacy unmarked admissions. Older dispatchers’ refusal of new marked admissions must be verified against concrete supported revisions.

Old producer writes invalidate new-format provenance. Re-upgrade must establish fresh continuity through producer-owned transitions, including the pre-refresh validation rule above. It must not resurrect generations from retained identity metadata or require destructive file deletion.

## 7. Preserved requirements

This revision does not change:

- Included-first selection or eligible provider-managed fallback.
- Unknown quota, demand, cost and served details remaining unknown.
- Genuine shared-pool accounting and terminal exposure fences.
- Explicit choices, provider refusals, one-use permits and uncertain-send protection.
- Account settings, authentication, permissions or spending controls.
- All-five-path scope; unsupported OpenCode identity remains blocked.
- Direct API protections, including the 20% buffer; production OpenRouter remains deferred.
- Existing no-live, qualification, activation and release boundaries.

Unobserved external login changes remain outside cached visibility. No atomic backend-identity guarantee is introduced.

## 8. Required proof before acceptance

Authorized implementation verification must cover:

- Same-second A→B and A→B→A.
- Same-account refresh, pending states, refusals and store re-creation.
- Old-writer success/clear followed by re-upgrade.
- Partial publication and crash boundaries.
- Eligibility/readings mismatch during collection.
- Immediate, split-call and continuation dispatch.
- Missing, pruned and legacy roots.
- Legacy unsent refusal without disturbing uncertain exposure.
- Concrete downgrade/mixed-version behavior and unchanged historical replay.
- Existing funding, lifecycle and storage-size constraints.

Implementation, compatibility and crash-safety properties remain proof obligations,
not results of design approval. Synthetic proof grants no live authority.
