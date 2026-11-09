# External instruction loading

The paired benchmark's Graphify read lost 5,767 tokens inside a mixed child response. Vibe's existing recovery notice reached model context, but the model continued without recovery. The fix must prevent that read shape through Vibe while preserving external sources, their authority, all applicable instructions and the user's execution settings.

## Approach

Extend the existing bounded reader with explicit `--external` selection of absolute local Markdown files outside the package. Derive sections and a revision from only those selected files in memory, then reuse existing page/batch packing and exact continuation. Route applicable external instruction files through this mode. Ordinary fitting reads remain one command; larger sources need complete continuations. Necessary source text consumes tokens; zero additional total tokens cannot be promised against an incomplete read.

Rewording the existing advisory alone repeats the demonstrated failure. Stateful gates or automatic hook-side file recovery add authority and overhead. The existing reader offers the smallest structural change; it remains a cooperative route whose execution must be observed.

## Constraints and proof

- Preserve packaged manifest validation and path restrictions; external mode cannot bypass them for files inside the package.
- Accept explicitly selected existing regular Markdown files, including paths with spaces and Unicode. Reject relative paths, URLs, non-Markdown targets, control characters and package-contained targets. Do not discover, follow references, execute, rewrite or prepare external files.
- Bind continuation to ordered selected paths, resolved targets and current exact source bytes. Read each selected source once per invocation. Validate the whole selection before emitting any text.
- Preserve 8,000-byte source sections, 9,500-byte single pages and 24,000-byte rendered batches, including headers and continuation. Preserve single-page fallback and native recovery when a source cannot fit the reader's section contract.
- Identify external source paths and continuation mode explicitly. Package sources retain their existing output/API defaults.
- Keep RTK, other plugins, authentication, model and reasoning unchanged. Add no dependency, hook, cache, persistent state, sizing probe or polling.
- Test exact UTF-8/line-ending reconstruction, unchanged one-call fitting reads, source/selection/target staleness, controlled failure without partial output and package compatibility. Independently review the change and rerun one identical native before/after task with implementation, separate review and one resume.

Authorized scope: the user's request to fix missed Graphify recovery through Vibe. Root implements this cohesive slice; independent native review verifies it. No live installation or merge is selected by this change.
