# Clean dev package: Codex qualification

9 November 2026. Qualifies installing the plugin from the clean export built by `scripts/package.py`, and its startup hook, on Codex CLI `0.167.0` through `codex app-server --listen stdio://` on macOS. It does not cover the Desktop UI, other hook events or Claude Code.

## Candidate

- Export of `clean-dev-package` at `42b813c` in `~/.cache/vibe-workflow/dev-package`: 541 tracked files totalling 9,457,813 bytes (10 MB on disk), package validation passed.
- Package digest `b9d33b418fbf175116cb138c56cba1370c1ecd5e8d20fa2e4cb576302217eea2`: SHA-256 of the output of `find . -type f -print0 | sort -z | xargs -0 shasum -a 256`, run inside the export.
- The installed copy was byte-identical to the export (`diff -r`), with no ignored files, `.git` or enrollment state.

## Method

A disposable, unauthenticated `CODEX_HOME` registered the export with `codex plugin marketplace add` and installed it with `codex plugin add`. A legacy schema-1 enrolled fixture and an unrelated Git repository served as working directories. A driver called `hooks/list`, wrote trust through `config/value/write` with the listed `currentHash`, then ran `thread/start` and `turn/start` and collected `hook/started` and `hook/completed`. Without credentials every model request failed with 401 after the hook ran, so no model usage occurred.

On this version `thread/start` alone does not run SessionStart; the first turn does.

## Observed

| Case | Result |
| --- | --- |
| Discovery after install | Five plugin hooks from the installed `hooks/codex.json`, all enabled and `untrusted` |
| Untrusted, enrolled fixture | No hook ran |
| Trusted, enrolled fixture | SessionStart completed in 115 ms with one context entry pointing at the installed package's `clients/codex.md`, policy index and `read_instructions.py` |
| Trusted, unrelated repository | SessionStart completed in 178 ms with no entries |
| Hook definition changed after trust | Listed as `modified`; the run failed in 29 ms with `plugin contents changed; hook approval required` and added no context |

Disposable homes and fixtures were removed afterwards.

## Not established

- Desktop UI behavior, PreToolUse, PostToolUse, Stop and Interrupt (no tool call or completed turn ran), timing distributions and Claude Code execution.
- The user's own Codex home: after moving to the export it lists all five hooks as `modified`, because the trusted fingerprint covers the earlier package content. Startup stays skipped there until the user approves the hooks again.
