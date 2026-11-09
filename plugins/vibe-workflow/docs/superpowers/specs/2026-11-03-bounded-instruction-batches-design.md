# Bounded instruction batches

The user approved bounded batches in the existing reader, preserving requirements, followed by independent review and the same implementation/review/resume benchmark. RTK, other plugins, model settings and authentication remain unchanged.

Keep applicability selection with the coordinator. After selection, let the existing reader combine consecutive prepared pages without requiring a model decision for each page. Preserve exact source text, source order, duplicate elimination, path containment, complete range validation and revision-bound continuation. Validate the selected sources before emitting anything; a batch must not combine separate source observations.

Retain the existing single-page interface and its 8,000-byte source and 9,500-byte rendered limits. Add `--batch` and `load_batch(root, paths, page=1, revision=None)`. A batch starts at the existing page number, includes consecutive complete pages, and reports its last included page. Continuation starts at that page plus one, with the same selection, revision and batch mode. The fallback can resume with ordinary single-page reads.

Use a provisional 24,000-byte limit for the entire rendered batch, including headings and continuation metadata. This is an implementation choice to qualify through the actual native child and outer output boundaries, not a bytes-to-tokens equivalence or a universal client guarantee. Keep child `max_output_tokens=10000` and outer `functions.exec max_output_tokens=12000`; forward one batch alone. Do not route routine loading to batching until exact model-visible delivery is verified. If the boundary proof fails, reassess the bound before any benchmark, escalating recurring validation churn to Astra.

No new hook, agent, dependency, runtime database, cache, sizing probe, automatic recovery loop or task repin. Bootstrap remains a separate required read before task work, including independent review; complete current retained text still permits reuse. On batch delivery failure, retain complete material and use bounded single-page/native reads for missing text. Required reviews and freshness checks remain binding.

Alternatives considered: line-boundary packing alone saves only one page under the recorded selections; startup inlining risks hook limits and duplicate resume context. Batching targets the eight mechanical continuation decisions observed in the previous trial while retaining the current safety path.

Validation must cover UTF-8 exact reconstruction, ordered pages, metadata within the aggregate bound, stale or missing continuation revisions, changed selection, no partial stdout on failure, and unchanged small-page behavior. The benchmark uses merged main as baseline in both phases, including its naming-contract triggers. Compare complete applicable delivery, task checks, calls, normalized usage and elapsed time; do not infer savings from page counts.
