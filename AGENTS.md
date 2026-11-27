# Smart Router

This repository contains only an OpenCode V2 advisory selector. Start with
`README.md`; read `docs/development.md` before edits or check selection.
Source provenance and the exact extraction inventory live in `docs/dependencies.md`.

- Keep the smallest sufficient change. Reuse the standard library and existing helpers.
- Keep `README.md` short and limited to what users need. Update it only through the `impeccable` skill.
- Preserve credentials, native configuration, other plugins, local changes and private evidence.
- Selection never launches, admits, reserves quota or grants a spend permit.
- Keep tasks, agent roles, reviews, permissions and execution with the caller.
- Use separate task worktrees. Do not install, activate, publish or run inference without explicit authorization.
- Exception: when `tests/tooling/test_opencode_version.py` fails, or a plan changes the injected notes, run `python3 -B scripts/live_check.py --injection` without asking. It may replace a retired recorded model with one level-1 Smart Router pick on that subscription path. If it fails, stop and tell the user.
- Write a failing test before nontrivial behavior changes. Validate, commit, then run affected tests.
- Require independent behavior/security review for nontrivial work; report unavailable checks honestly.

Commands:

```sh
python3 -B scripts/validate.py
python3 -B -m unittest discover -s tests -t .
node --test "tests/**/*.test.js"
python3 -B scripts/package.py --output dist
```

## Project layout

- `index.js` is the plugin entry and stays at the root, because `package.json` and `scripts/validate.py` require it.
- Every other plugin module lives in `src/`, one domain per file, named for what it does. Add a `src/<domain>/` folder only once a domain has several files.
- `runtime/` is the import-only Python library that ships. Keep it flat: `validate.py` allows only single-level `runtime.X` imports, and two import cycles (`claude_usage` and `native_catalog`; `routing_observations` and `opencode_account`) mean any package split would be cyclic or layered. Revisit when the cycles are gone or it passes about 25 modules.
- `scripts/` holds command entry points. Only `select.py` ships; `validate`, `package`, `manifest` and `live_check` are development tools.
- `tests/` groups by what it covers: `plugin/` and `skills/` hold Node `<module>.test.js`; `runtime/` and `tooling/` hold Python `test_<module>.py`, and `tooling/` also holds the dev-tool checks and `opencode-verified.json`. Only `__init__.py` stays at the top. Run Python with `-t .` (see the commands above).
- `skills/<id>/` holds `SKILL.md` plus `references/`; `docs/` holds specs and plans under `superpowers/` and council records under `vibe-workflow/council-sessions/`.
- A new shipped file must be covered by `package.json` `files` (a directory entry covers the files inside it) and listed in `scripts/manifest.py` in the same change.

The active extraction task retains its original pinned policy and local-only
delivery constraints in its plan, design and private ledger. This shorter product
entry does not waive those task requirements.

## Memory and project preferences

- Project preferences: `.vibe-workflow/settings.json` is owned by the confirmed
  `vibe-onboard-project` flow. This repository does not ship legacy feature
  settings commands.
- Memory: the user-level vault `~/ai-memory-vault` is plain files, with no vault software or MCP server. Before project work, read its `MEMORY.md` index and open topic files only when relevant. Before saving, updating, migrating or pruning, read its `INSTRUCTIONS.md` in full and follow its narrow test and growth policy. Keep this section intact; it is the vault's load path.
