# API usage activation

The user approved simplifying activation by reusing existing setup, fixing the
cross-project policy invalidation bug, and the public name **Manage API Usage**.
This is source implementation authority, not authorization to activate a project,
change billing, read credentials, spend credits, or publish a release.

## Outcome

An explicit enable request can attach an enrolled checkout to an applicable
existing setup without repeating connection or funding setup. Binding remains an
internal accounting step. Existing projects keep their activation when another
checkout uses the same funding policy.

Keep the current ledger, charges, outstanding reservations, account connection,
funding policy, expiry and chosen caps. Do not add a mandatory dollar cap or smoke
test. New project activation and task/data permission remain project scoped; do
not copy another project's live consent, source permission or qualification.

## Approach

Use the existing settings/controller and shared organization ledger. Identical
validated funding policies must reuse their current revision; actual policy
changes must retain revision invalidation. Add a bounded reuse path to normal
enablement, selecting a unique compatible setup or reporting ambiguity. Explicit
selection may disambiguate existing setups. Status remains read-only.

Keep explicit per-checkout activation and disable. Reuse does not extend
enrollment to another repository, clone, client or worktree. Missing or replaced
ledger authority requires recovery; it must never create another allowance.
Stale policy, expired settings and smoke-only budgets cannot become live regular
routing through reuse. Existing configured and disabled checkouts retain their
own settings and revision checks.

Rename the existing skill's public heading and UI display to **Manage API Usage**.
Describe credit usage, spending limits and routing in discovery metadata. Keep
the internal `manage-usage-routing` identifier and existing reference paths for
compatibility. Update current human guidance and source-to-contract coverage.

## Alternatives and limits

Manual setup per checkout repeats already accepted decisions. Repository-wide
automatic activation needs broader consent and disable precedence, so retain
checkout activation with reusable account setup. A new global profile service
would duplicate the current shared ledger. Keep existing binding identities;
changing enrollment hashing and migrating historical bindings are separate work.

## Proof

Use disposable ledgers and fake connections to prove that two checkouts stay
active under an unchanged shared policy, changed policies still invalidate stale
consent, reuse preserves accounting and permissions, and ambiguous or stale setup
does not enable. Validate package and skill metadata, and walk representative
instruction-loading requests. No live provider request is needed.
