# Prepared Vibe instruction loading

The user approved implementing Vibe's prepared loading, preserving every requirement, independently reviewing and validating it, and repeating the existing paired benchmark. RTK, other plugins and their skills remain unchanged.

## Design

Reuse the canonical client documents, policy index, policy triggers and references. An authoring command prepares a manifest of their content digests and contiguous line ranges. The manifest contains no copied instructions or project state. Package validation rejects stale or incomplete preparation.

A local, read-only reader returns exact source text in bounded pages. It accepts only packaged instruction paths, deduplicates repeated selections, validates selected source digests and contiguous coverage, and reports page identity, continuation and completion. Continuations bind the selected sources to a revision; source changes require a new selection. No database, installation, network, model call or automatic retry is involved. Project configuration and local instructions retain their existing authority and native reading paths.

Codex startup supplies the reader command before instruction fanout. Its bootstrap selects the complete Codex guide and policy index. Subsequent reads select only triggered contracts/references. Each response travels alone through the outer tool boundary; a reader cannot enforce arbitrary callers that ignore this route. Preserve complete material already in context; a historical read does not prove availability after compaction.

Prepared source segments contain at most 8,000 UTF-8 bytes, split on complete lines. The reader packs selected segments within that source budget and bounds the final rendered response to 9,500 bytes. Request 10,000 child output tokens and 12,000 outer output tokens, forwarding the output once without another batch. These are conservative delivery settings for the observed client, not a guarantee about all native clients or external tools. Unusually long individual lines fail preparation instead of being silently clipped.

## Approaches

Further prose/advisories cannot reliably prevent the observed oversized reads. Native pagination could cover all plugins but is outside this change. The selected approach prepares exact Vibe sections and a bounded local delivery path, building on the existing index without changing source requirements or adding mandatory runtime controls.

## Acceptance

Tests prove exact reconstruction, bounded pages, ordered continuation, rejection of changed sources and invalid paths, selective reads, and unchanged startup scope. Package validation checks the manifest. Independent review checks requirement coverage and security. The unchanged coding/review/resume benchmark measures actual adoption, complete model-visible Vibe text, unnecessary rereads, clipping, tokens and time. No savings claim follows from code structure alone.
