# Workstreams on the main runtime

This port adds managed Workstreams to main's existing Codex schema-2 task runtime. It includes saved enable/disable/status controls, child reuse and one-use continuations, native naming, parent capacity-recovery bookkeeping, cooperative CPU/memory admission and the reviewed efficiency changes. New supported runtime scopes start enabled; existing preferences and missing legacy settings retain their choice. Use Workstreams only when beneficial.

The existing [skill](../skills/workstreams/SKILL.md) routes naming, management and execution. [Execution](../clients/workstreams.md), [dispatch](../clients/workstreams-protocol.md), [resources](../clients/workstreams-resources.md), [parent recovery](../clients/workstreams-recovery.md) and [title repair](../clients/workstreams-titles.md) define the complete local contracts. Ordinary delegation remains available when mode is off. Naming does not enable execution.

## Integration boundary

The port retains main's task/evidence/delegation storage and immutable pins. It does not import the unmerged development ancestry, canonical schema-3 lifecycle and bookkeeping transfer, accounting, OpenRouter or observability features. Existing supported native settings, credentials, permissions and subscriptions remain unchanged. Unsupported authority is rejected rather than silently adopted or rewritten.

Historical tasks remain attached to their creating runtime and authority. Use the existing approved fresh-state transition; never copy live records, reset attempts, erase unresolved owners or infer that a display title authorizes work. This port makes no canonical-3 historical recovery claim.

## Validation boundary

Qualification records must identify this port's exact package/runtime and actual source tests. Earlier full-development or Naming-only results establish neither this port's complete regression result nor its native execution. Source/package validation and actual native callbacks are separate checks. A later package needs its own observed native evidence before a conditioned push; do not relabel earlier fixtures.

Compact replies and fewer repeated reads/probes are implementation improvements, not measured monetary savings or a universal speed guarantee. Original immutable continuation text remains available at admitted consumption. Shared pressure, disk vetoes, fresh authority, ownership, budgets and uncertain delivery remain protected.

## Hybrid review handoffs · 5 November 2026

The user selected child-requested reviews, coordinator-spawned parallel reviewers,
child-owned fixes and coordinator acceptance after comparing centralized, nested
and hybrid review. The hybrid keeps independent evidence and global integration
with the coordinator while ready outcomes receive separate reviews concurrently.
Fully nested review would require additional managed provenance support.

The implementation defines the handoff once in
[Agent Delegation](../policies/03-agent-delegation.md#child-requested-reviews).
The Workstreams entry point loads that policy directly for ordinary child work;
managed execution links to the same procedure. Review requests reuse existing
handoffs, original task allowances and selected review evidence controls. The
runtime, schemas, feature preferences, permissions and model choices are unchanged.

Validation on 5 November 2026:

- `python3 -B scripts/validate.py` and Skill Creator's `quick_validate.py skills/workstreams` passed. The entry point has 140 words; seven added local links and anchors resolved.
- The focused run used `tests.test_workstreams`, `tests.test_native_reviews`, `tests.test_delegation.Stage4NativeReviewTests` and the admission tests `test_disjoint_claims_respect_capacity` and `test_retry_preserves_original_finite_attempt_and_resource_budgets`. Of 87 cases, 86 passed initially. `test_superseded_review_findings_survive_failed_ownership_recovery` detected candidate movement while the coordinator edited policy during execution; it passed when rerun with files frozen. No runtime or test code was changed.
- Independent instruction application covered a child's review request, parallel reviews while unrelated work continues, repairs with retained reader claims and consumed budgets, and ordinary review with both optional features disabled. All four proposed sequences preserved the required responsibilities and evidence. These were instruction walkthroughs over source snapshots, not executed managed lifecycles.
- Independent diff review found no substantiated defects. A scoped follow-up accepted the explicit fixed-candidate condition. Subsequent changes only record validation and clarify wording.

Package checks and instruction walkthroughs do not establish installed-client
execution, a live managed lifecycle or a measured speedup. That source-only
validation did not install a package, add controls or rewrite active task pins.

### Installed update and live native pilot

On 5 November 2026, the user authorized installation and a live hybrid-review
test. Local build 0.1.30 applies the reviewed handoff changes to installed 0.1.29,
preserving its newer contracts and runtime rather than replacing them with this
older checkout. Native plugin installation confirmed 0.1.30. All 327 payload files
matched the staged candidate; native installer Git metadata was excluded.

Codex's native hook review trusted the five definitions at their new package
path. Only their five stored trust hashes changed in user configuration. Model,
reasoning, permissions, integrations, feature preferences and the user hook file
were preserved. Older cached versions were restored after native installation
cleanup and verified byte-for-byte; active task pins and runtime records were not
rewritten. The published marketplace release was not changed.

The ordinary native pilot used two synthetic outcomes. Both original children
requested independent review; two root-spawned reviewers overlapped for 44 seconds
and reproduced the seeded boundary defects without editing candidates. Repairs
returned to the same child identities. The same reviewers rechecked those fixes;
the coordinator retained original reports, reran all nine full-check cases with
a credential-free environment, checked combined behavior and accepted both.

The original shared allowance was four child contexts, two concurrent children,
eight turns, two review rounds, two repair turns and two scoped rechecks, with a
20-minute deadline. The pilot used those allowances without a reset and completed
in approximately 688 seconds. These were coordinator-maintained ordinary-native
budgets, not hard token limits or a new managed admission/control qualification.
Native identities, original findings, rechecks, candidate hashes, acceptance and
configuration comparisons remain in the private installation record outside
projects. The live result does not establish a general speedup or a restarted
Desktop session; a later marketplace refresh may replace the local build.
