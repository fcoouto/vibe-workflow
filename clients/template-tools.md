# Selective template assistance

Load for selected template adaptation or required context transported between checkouts. The [onboarding owner](../skills/onboard-my-project/references/procedure.md#adapt-project-documents) retains document mapping and semantic decisions; [delegation](../policies/03-agent-delegation.md#progress-and-result-retrieval) retains dependency acceptance. Ordinary changes without these triggers do not load or run this helper.

## Prepare selected shapes

Resolve `<package>` from the installed workflow skill/client pointer, not the target project. Use the existing document mapping to select only shapes needed for authorized additions or repairs. Supply paths relative to `<package>/templates/`; context paths are relative to the actual consumer checkout. No Git repository, database or nine-file layout is required.

```text
python3 -I -B <package>/scripts/template_tools.py prepare --cwd <consumer> --template project/product.md --context AGENTS.md
```

The JSON contains observed time, required-context SHA-256 digests, a combined `revision`, and the prepared reader's exact `instructions`. It verifies availability but does not deliver context-file text: inspect needed context through existing native reads. Follow `instructions.last_page` and `instructions.pages`; request the next page with the same selection and top-level `--revision`. Changed consumer context or prepared sources require a new preparation. Keep receipts in existing private task/evidence records, not another project document.

## Check a document batch

Use the existing protected, project-relative before-images from authorized setup/recovery. A readable before-directory must exist; an absent selected path there means a new file. Without `--before`, preservation is `not_checked`. The caller establishes that originals are complete and protected; the helper cannot authenticate a baseline.

```text
python3 -I -B <package>/scripts/template_tools.py check --cwd <consumer> --document README.md --document handbook/development.md --template project/product.md --template project/local-preparation.md --before <protected-before-directory> --quiet
```

Run once after the explicit batch, before reporting its checks. Exit 0 means checked mechanical conditions passed; `--quiet` emits nothing on success. Exit 1 returns bounded findings; exit 2 means the operation was unavailable or invalid. Inspect findings against source and originals, repair authorized defects, and rerun only affected selections. An intentional original-line change needs its existing authorization and review, not automatic restoration or deletion. A green result establishes neither semantic preservation nor factual accuracy, acceptance, startup or agent routing.

Checks cover exact nonblank original lines and multiplicity (reordering allowed), exact placeholders from selected templates, recognizable added author directions, and local file links. Existing author-like lines remain exempt from residue checks when their before-images match. Link targets resolve from each destination; linked files are checked directly, never crawled recursively or fetched from the network. Basic inline/reference links, ATX heading anchors, percent decoding and duplicate headings are supported. HTML, setext headings, directory links and other renderer extensions need native inspection; this is not a full Markdown parser.

Selections and all accessed file paths, including linked targets, templates, the prepared map and before-images, are limited to 64 per operation; each file is at most 1 MiB, total unique input at most 4 MiB. Split large batches instead of truncating evidence. Roots are caller-selected directory anchors; descendants must be relative regular files, without symlinks or traversal. The installed package is trusted and must stay unchanged while its existing prepared reader runs. Findings contain selected paths, codes and line numbers, not project excerpts; keep private paths in private evidence.

## Consumer context

Before launching work that depends on transported context, compare the producer's retained, accepted digests against the actual consumer copies:

```text
python3 -I -B <package>/scripts/template_tools.py check --cwd <consumer> --require rules.md=<accepted-sha256> --quiet
```

Repeat `--require` for each required path. Do not derive expected hashes from the consumer copy being checked. A missing or changed file holds only its dependent launch; transfer the accepted copy through the existing authorized route, then recheck. Reuse equivalent prepared-assignment evidence rather than running a duplicate check. Availability and byte identity grant no permissions and do not prove that a worker read or understood the context.

## Callable feedback boundary

`runtime.template_tools.notice(result, previous=None)` returns at most 1,000 characters, stays silent on success, and suppresses only a matching non-null fingerprint from the caller's previous result. The fingerprint includes checked content, dependencies, before-images and observed absences. Unreadable or oversized content has no reusable fingerprint. Callers own any saved fingerprint and must scope it to the same task/check selection; there is no helper state database.

No native hook is installed. A future adapter must first prove usefulness and qualify actual client invocation and delivery. Trigger only for an explicitly tracked document batch or dependency launch; ignore unrelated edits, avoid repeated reads, keep success silent and feedback bounded. Do not block Stop by default, start repair/model loops, register hooks or change permissions from this procedure. Offline replay proves the callable helper only; model-token savings and native hook behavior need separately authorized observations.
