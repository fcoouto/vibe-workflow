# Automatic instruction draining

The accepted assessment demonstrated that one awaited native JavaScript call can deliver all 794 Graphify lines through separate notifications. The previous reader correctly returned a continuation, but the model switched selections before requesting it. The user has authorized implementing automatic draining, reviewing and validating it, then running the same paired benchmark.

## Approach

Use the existing reader for every batch, retaining current-source and revision validation. The Codex loading contract supplies one canonical native bootstrap. It reads the first batch as a machine envelope, emits and returns immediately for a complete selection, or loads a small fixed Vibe-owned JavaScript helper inside that same call. The awaited helper owns every remaining request and emits each verified batch with `notify`. Only a compact receipt uses the direct result.

Alternatives considered: a shell loop would recreate aggregate truncation; another reminder leaves the model's continuation decision intact. A one-snapshot reader could reduce file rereads but changes the existing freshness boundary. Neither is selected.

## Interfaces and requirements

- `read_instructions.py --json` returns a versioned envelope with `kind`, exact `text`, its Unicode character count, `page`, `last_page`, `pages`, `revision`, `external` and `batch`. Plain output and existing APIs stay compatible.
- `read_instructions.py --helper` returns the same text/count envelope for the fixed packaged helper only; it accepts no instruction selection or preparation/read flags.
- `scripts/drain_instructions.js` is an async function evaluated only from that fixed trusted helper envelope. Inject the original selection's read callback, envelope decoder, first parsed batch (or null for clipped output), and native notification callback. Never execute instruction content or a footer command.
- The bootstrap is the only place defining command construction and envelope decoding. The helper preserves ordered page progression, original mode/selection, fixed revision and total. A fitting first batch needs no helper read.
- JSON parsing and positive character-count validation detect incomplete child bodies before notification. Source metadata cannot control commands. Unfinished children and nonzero exits fail immediately. A damaged batch receives one single-page attempt at the same missing page; once fallback starts, finish using single pages. A damaged small page fails explicitly. Do not duplicate previously emitted ranges.
- Preserve 8,000-byte source sections, 9,500-byte rendered small pages, 24,000-byte rendered batches, exact Unicode/line endings, source authority, applicability, path/hash checks and resolved-target freshness. Changed revisions fail without a completion receipt.
- Keep all work in the awaited cell; if the host yields a running cell, wait for its terminal result. Cancellation or rejection never counts as complete. This is not a universal gate on model behavior or a guarantee of initial adoption.
- Add no hook, server, dependency, persistent cache, background monitor, enrollment or task-runtime layer. RTK, other plugins, native account, model and reasoning remain unchanged. Other clients keep their existing routes.

## Proof and overhead

Test the actual bootstrap and helper with real reader subprocesses, including exact reconstruction, the single-batch fast path, clipping/fallback, stale source and target changes, fake source metadata, shell-sensitive names, invalid metadata and pending/cancelled calls. Qualify model-visible notification delivery for the original four-source selection and existing maximum rendered batch size. Obtain independent review and package validation before the paired benchmark.

The benchmark uses baseline `385ff3e` and the reviewed candidate, with Vibe enabled in both. Keep the same implementation, independent review and one resume, native settings and strict external-state guard. Run one pair; stop on drift or timeout and escalate any repeated validation churn to Astra. Report source/package checks separately from observed native behavior and normalized usage. No merge or live installation is selected.

For N complete batches, reader calls and source rereads remain N. Multiple batches add one helper command and remove N−1 model continuation decisions; a fitting selection adds no helper command. Required text remains intact. Wrapper, helper and receipt costs must be measured; neither zero total overhead nor savings against incomplete delivery is promised.
