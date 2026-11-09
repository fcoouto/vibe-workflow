# Instruction scope routing and recovery budgets

The user authorized fixing mixed-scope selections and recovery budgets through Vibe while preserving automatic draining, all source checks, RTK, other plugins and model settings. The observed failure combined an external guide and an absolute Vibe contract under a model-chosen external flag. The reader correctly rejected it, but the model's direct recovery combined child budgets without enough outer headroom.

## Design

Add `--auto` to the existing Python reader and make the packaged runner always use it. The model supplies only explicit applicable paths and the package root. Python classifies each source, validates it under its existing authority, and delivers the ordered selection through the existing bounded pages and notifications. There is no separate resolver process, source discovery, scope grouping, cache or raw recovery API.

Relative paths always retain package authority and their original prepared-map key. For absolute paths, inspect the resolved target and lexical ancestors, including package aliases. A path originating in the package may not escape it through a symlink or `..`. An absolute path or outside alias resolving inside the package must use a prepared package entry and hash. Where the package-local alias key is derivable, retain it instead of silently switching to the target entry. Only paths outside the package throughout their ancestry use the existing external Markdown checks.

Automatic revisions bind the mode, ordered original names, each source's authority, prepared key where applicable, resolved target and current entry/hash/ranges. Every continuation re-resolves and revalidates them. Legacy explicit package/external modes and their revisions remain compatible; `--auto` is mutually exclusive with `--external` and cannot accompany helper loading or preparation. JSON adds a mode discriminator; automatic packets require `mode: "auto"` and the legacy external flag remains false because `--external` was not used.

This removes the observed mixed-scope failure and its unsafe direct-recovery detour. Transport clipping continues to use the packaged runner's bounded small-page recovery and separate notifications, with unchanged child limits and compact direct receipts. Keep exceptional native recovery for reader or unrecoverable transport failure: deliver one bounded read per outer call, with a 10,000-token child and 12,000-token outer budget, or its own notification. Never aggregate recovery bodies. This replaces the existing generic recovery guidance; it does not introduce a second procedure or claim control over arbitrary model-authored calls.

## Preserved boundaries

Keep exact Unicode and line endings; 8,000/9,500/24,000-byte bounds; package manifest/hash/instruction-path containment; explicit external regular-Markdown validation; ordered pages, fixed revision/total/mode; positive complete character counts; no replay after delivered pages; awaited notifications and terminal completion; and fail-closed behavior for changed sources, invalid metadata, cancellation or unfinished calls. No instruction text becomes code. Required source selection and semantic compliance remain distinct from delivery.

Alternatives rejected: another mode reminder leaves the recurring decision with the model; a separate resolver/group dispatcher adds commands and partial-group state; accepting package paths in legacy external mode bypasses their prepared authority. Existing exceptional fallback must not be removed or made a hash-bypass API.

## Proof and scope

Test mixed ordered reconstruction, package aliases/escapes, unchanged legacy modes, revision/mode/target changes, invalid later sources with no partial stdout, exclusive flags and real-runner clipping recovery with fixed notification budgets. Obtain fresh independent review, focused reader/drain/startup/package checks and package validation. Qualify actual mixed-source native delivery and forced clipping/yield, then run one fresh paired implementation/review/resume benchmark against `2179691`. Stop on drift/timeout; escalate validation churn to Astra. No merge, live installation, full-suite repair campaign or claim of universal model adherence is selected.
