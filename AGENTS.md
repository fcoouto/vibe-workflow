# Smart Router

This repository contains only an OpenCode V2 advisory selector. Start with
`README.md`; read `docs/development.md` before edits or check selection.
Source provenance and the exact extraction inventory live in `docs/dependencies.md`.

- Keep the smallest sufficient change. Reuse the standard library and existing helpers.
- Preserve credentials, native configuration, other plugins, local changes and private evidence.
- Selection never launches, admits, reserves quota or grants a spend permit.
- Keep tasks, agent roles, reviews, permissions and execution with the caller.
- Use separate task worktrees. Do not install, activate, publish or run inference without explicit authorization.
- Write a failing test before nontrivial behavior changes. Validate, commit, then run affected tests.
- Require independent behavior/security review for nontrivial work; report unavailable checks honestly.

Commands:

```sh
python3 -B scripts/validate.py
python3 -B -m unittest discover -s tests
node --test tests/*.test.js
python3 -B scripts/package.py --output dist
```

The active extraction task retains its original pinned policy and local-only
delivery constraints in its plan, design and private ledger. This shorter product
entry does not waive those task requirements.

## Memory and project preferences

- Project preferences: `.vibe-workflow/settings.json` is owned by the confirmed
  `vibe-onboard-project` flow. This repository does not ship legacy feature
  settings commands.
- Memory: the user-level vault `~/ai-memory-vault` is plain files, with no vault software or MCP server. Before project work, read its `MEMORY.md` index and open topic files only when relevant. Before saving, updating, migrating or pruning, read its `INSTRUCTIONS.md` in full and follow its narrow test and growth policy. Keep this section intact; it is the vault's load path.
