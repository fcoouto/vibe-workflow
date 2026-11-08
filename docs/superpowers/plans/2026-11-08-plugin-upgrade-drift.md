# Plugin upgrade across clients — design and plan

Date: 2026-11-08. Status: approved by the user for implementation, review and merge.

## Problem

Each supported client keeps its own copy of the plugin and upgrades it with its own command. Codex copies the package into a versioned cache at install time and its **Upgrade** works only for a Git marketplace; Claude Code refreshes through `claude plugin update`. Nothing upgrades both, and nothing notices when one lags. Observed on 2026-11-08: Codex ran a cached 0.1.0 while Claude Code ran 0.1.32 from the same checkout, and a removed Codex install went unnoticed until checked by hand.

## Design

1. **One upgrade procedure**, owned by `prepare-my-workspace` (machine preparation already owns client integrations). For every supported client it runs that client's official update command, re-reads each installed version through the client's own CLI, and reports four separate states per client: installed version, install source, hooks trusted, startup observed. A missing or failed client is reported as not upgraded, never skipped.
2. **Expected-version record.** After the readback shows every installed supported client at the package version, `workflow.py upgrade record` writes a small private machine-level file outside any repository: `$XDG_STATE_HOME/vibe-workflow/package-release.json` (default `~/.local/state/vibe-workflow/`), mode 0600, atomic replace. It refuses to record while any installed client differs.
3. **Startup drift warning (the enforcement).** When a client's `SessionStart` already produces output for an enrolled checkout, it compares its own manifest version with the record. A difference appends one plain warning naming the client, its version, the expected version and the next action. It reads one small file, writes nothing, makes no network or subprocess call, and any error leaves the startup result unchanged. No record means no warning.
4. **Status on demand.** `workflow.py upgrade status` returns the package version, the record and each client's observed install (`unknown` when the CLI is missing or its output cannot be parsed), without writing.
5. **Unchanged:** `validate.py` already requires both manifests to carry the same version. No new dependency, hook, network call, runtime database or automatic upgrade. Installing from the Git release marketplace is recommended in the procedure but stays the user's choice.

## Plan

Files: `runtime/package_versions.py` (new), `scripts/workflow.py`, `skills/prepare-my-workspace/SKILL.md` (stay within 150 words), `skills/prepare-my-workspace/references/upgrade.md` (new), `clients/instruction-map.json` (regenerated), `tests/test_package_versions.py` (new).

1. Tests first: CLI output parsing for both clients (observed formats), record write/refusal/atomicity/permissions, `status` makes no writes, startup warning appended on mismatch, silent when the record is absent or corrupt, startup never writes or spawns a process, unenrolled startup stays silent.
2. Implement the module, the `upgrade status|record` controls and the startup comparison.
3. Write the procedure and link it from the skill.
4. Checks: `scripts/validate.py` and every test module that exercises `scripts/workflow.py`, startup output or the changed instructions, run in parallel. The full suite is not required.
5. Independent read-only review, then merge.

## Acceptance

- With a record at 0.1.33 and a client manifest at 0.1.0, that client's startup output carries the drift warning; with matching versions or no record it carries none.
- `upgrade record` refuses while one installed client reports another version, and succeeds once all match.
- Package and unit checks pass. Observed client behavior after a real upgrade is reported separately and stays pending until seen.
