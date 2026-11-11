# Native hook and token pilot protocol

Approved on 11 November 2026: at most 12 fresh sessions, two concurrent, 45 minutes from the first live launch, existing subscriptions only. The detailed [plan](../../superpowers/plans/2026-11-11-template-hook-pilot.md) was subsequently approved for inline execution.

## Frozen comparisons

Use the unchanged bundled `phase-two/minimal/input` and `phase-two/customized/input` fixtures. All arms receive the same original files, three selected templates (`project/product.md`, `project/overview.md`, `project/local-preparation.md`), requirements, writable paths and quality criteria. No arm must produce the historical nine-document layout.

- Minimal: adapt only `README.md`, combining product, overview and preparation.
- Customized: adapt only `README.md`, `handbook/overview.md` and `handbook/engineering.md`; preserve the architecture and nested instruction owners.
- Baseline: native selective reads and prose checks.
- Helper-only: fixed-selection `template_prepare` and `template_check` native tools call the existing read-only helper.
- Helper-plus-hook: the same helper tools plus advisory after-edit feedback for the same selection.
- Order: minimal baseline/helper/hook, customized hook/helper/baseline. Fresh isolated runtime and project per session. This order is not randomized and provider cache state is not controllable.

Use OpenCode 2.2.6 with its discovered `openai/gpt-6-luna#low` ChatGPT-subscription route for all six comparisons, conditional on the native probe. The simple documentation assignments and deterministic checks justify the small model; the coordinator model stays unchanged. Configure 24 primary requests, no OpenCode retries, 8,000 maximum output tokens per response, four minutes per comparison, no automatic compaction and no title calls. These are host controls, not an upstream billing guarantee. The guard checks route, request count and absolute deadline immediately before requests. Observe actual transmitted requests and returned token fields.

## Native probes and trust

Run a three-minute OpenCode probe and a three-minute native Claude Code probe. Exercise a deliberately broken link, unchanged repeated observations, repair and unrelated work. Preserve original successful tool output and prove model-visible feedback separately from hook invocation. Direct helper and adapter self-checks do not count as native execution.

Codex 0.167.0 requires explicit hash-bound hook review. No supported reviewed trust exists yet for the disposable candidate. Hold this probe unless the supported review flow completes; never use `--dangerously-bypass-hook-trust`, fabricated trust files or administrator-managed hooks as a shortcut. A held preflight spends no live session.

Claude Code 2.1.311 uses reviewed per-invocation settings, a synthetic home, `--restricted`, `--strict-mcp-config`, file-only tools, scoped write permission and an existing subscription OAuth token held in memory. Use the documented `--max-turns 24`; this bounds agentic turns, not hidden upstream retries. Retain native usage and unknown retry accounting separately. Its process group has the same three-minute timeout and global deadline. No new account login, token minting, API-key route or global setting change.

## Isolation and checks

Use separate private directories outside the checkout. Native processes cannot read the real home or write outside their individual run directory. The copied helper package and protected before-images sit outside worker-writable directories. Remove arbitrary execution, network and delegation tools; the host still needs network for its authenticated subscription. This is not an OS-level provider-domain network allowlist. Native file permission rules additionally limit project writes to the named documents. Compare all original and added file paths independently afterward.

Acceptance requires preserved original nonblank lines, byte-identical protected files, no unexpected artifacts, working destination-relative links, no copied author guidance/placeholders, truthful product/runtime/check statements and an explicitly proposed next task with observable acceptance. Independently run the protected fixture check and inspect complete diffs. Mechanical checks do not establish semantic correctness.

Keep per-session prompts, source/configuration hashes, tool outputs, native receipts, failures and output snapshots privately. Share synthetic task data and sanitized evidence only; never publish credentials, auth responses or private client logs.

## Accounting and limits

Report uncached input, cache-read, cache-write, output and any reasoning subset separately according to observed provider semantics. Preserve raw counters. Never add a subset twice or use zero for missing values. Report requests, repair responses after findings, output quality, hook count/time and feedback bytes. Byte counts are not token savings.

Calculate baseline-to-helper and helper-to-hook deltas per complete matched case, with quality beside each result. Also report total observed pilot consumption, failed attempts and probe/review overhead by model. Identify unobserved coordinator or retry usage. Do not claim broad or causal savings from two task cases, unequal-quality outputs, different clients/models or missing counters.

Reserve one independent read-only review within the original allocation and deadline. Two diagnostic slots remain available only for a changed, evidence-backed diagnostic, not repeated attempts or treatment search. Expiry stops launches and live processes; it does not authorize a new allocation. Retain failures and hold incomplete qualifications.
