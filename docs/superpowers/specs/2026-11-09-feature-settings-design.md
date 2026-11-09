# Feature settings — design

- **Date:** 2026-11-09
- **Status:** Draft for user review. Brainstormed by the coordinator and an independent Astra (high) design partner; user decisions recorded in section 2.
- **Plan:** [phase 1](../plans/2026-11-09-feature-settings-phase-1.md)

## 1. Problem

Feature state is scattered. An inventory on 9 November 2026 found 28 settings in about 14 storage mechanisms with no shared layer:

- **Instruction blocks only agents can read.** The Workstreams preference is a checkbox in `AGENTS.md`, so it has no status command and nothing validates it. A rewrite of that file (`502896f`) deleted it without notice.
- **Duplicate sources of truth.** A runtime file `<state_dir>/workstreams-<scope>.json` also carries `enabled` and gates delegation sends. Smart Routing reads that file, not the block (`runtime/routing_settings.py:197`).
- **Private per-feature stores.** OpenRouter, Observability and Usage Routing each have their own private store, binding rules and CLI. Smart Routing has one settings file per OS user.
- **Duplicated plumbing.** At least six implementations of private atomic JSON writes, and inconsistent state roots and names (`~/.vcw-openrouter-consent`, `~/.local/state`, `~/.vibe-workflow`, `$XDG_STATE_HOME`).
- **Undeclared defaults.** No one place says which features are on by default.

## 2. Decisions

| Decision | Choice | Source |
|---|---|---|
| Settings location | The project-root `.vibe-workflow/` folder | User |
| Team-shared file | `.vibe-workflow/settings.json` is committed. `.gitignore` lists only the private items: `/.vibe-workflow/project.json` and `/.vibe-workflow/private/` | User |
| Layers in this design | Package defaults and project (team) settings only; no user or personal layer for now | User |
| Rollout | Phased: phase 1 builds the standard and moves Workstreams; consent stores move later, one per change | User |
| Consent | Never granted by a committed file; stays private and checkout-bound | Policy (`policies/01-project-scope.md`) and existing stores |

## 3. Model

**Feature.** A stable, independently configurable behavior, such as `workstreams`, `thread_naming`, `smart_routing`, `usage_routing`, `openrouter.proposals`, `openrouter.workers` or `observability.langfuse`. A skill is an interface to a feature's operations; installing or invoking a skill enables nothing. Mandatory policy requirements, such as reviews, are not features and cannot be disabled.

**Manifest.** Every feature ships a data-only manifest, `features/<id>.json`, in the package:

```json
{
  "manifest_version": 1,
  "id": "workstreams",
  "summary": "Automatic native child work for suitable tasks.",
  "skills": ["workstreams"],
  "default": {"enabled": false},
  "settings": {},
  "scopes": ["project"],
  "consent": "none",
  "requires": [],
  "control": "settings",
  "adapter": null,
  "procedure": "skills/workstreams/references/procedure.md"
}
```

- **`default`** is required. A feature with no declared default fails validation.
- **`settings`** declares typed values and their defaults; unknown keys are rejected.
- **`scopes`** lists where a value may be set. This design implements `project` only.
- **`consent`** is `none`, `transmits` or `spends`. Features other than `none` need a private grant to be active, whatever the settings say.
- **`requires`** names prerequisite features or capabilities (for example a managed runtime); unmet prerequisites make the feature unavailable, never silently on.
- **`control`** is `settings` when the feature's behavior reads the resolved value, `adapter` when its state still lives in its own store, or `fixed` for behavior with no off switch: core operations (`onboard-my-project`, `prepare-my-workspace`, `review-my-project`) and, until it reads settings, `thread_naming`. Only `settings` features accept writes through this standard; a value in the settings file for an `adapter` feature is reported as ignored, so the file never claims control it does not have.
- **`adapter`** names the package module that reports status for `adapter` features, and **`source`** describes where their state lives and at which scope (for example `{"store": "usage-ledger", "scope": "checkout"}`). `source` is descriptive; `scopes` lists only where this standard can write.

Manifests contain no code, expressions or paths outside the package. `scripts/validate.py` checks every manifest and requires every packaged skill to belong to at least one feature, so a new skill cannot ship without declaring its feature and default.

## 4. Layout and precedence

| File | Holds | In Git |
|---|---|---|
| `features/<id>.json` (package) | Defaults and schema | Shipped |
| `.vibe-workflow/settings.json` | Team settings for this project | Committed |
| `.vibe-workflow/project.json` | Enrollment (unchanged, schema 2) | Ignored |
| `.vibe-workflow/private/` | Settings lock and migration backups (phase 1); checkout-bound grants and revocations (phase 2) | Ignored |

Effective value: **package default < project settings**. A missing entry inherits the default. Status always names the layer a value came from. The format reserves `user` and `checkout` scopes so they can be added later without a migration; until then they are rejected.

Everything private lives under `private/`, created with owner-only modes; no private temporary file is written anywhere else in `.vibe-workflow/`. Any other new file there is treated as shareable and must be reviewed as such. Before creating anything under `private/`, writers check the ignore rules with `git check-ignore --no-index` (which evaluates rules even for tracked paths) and, separately, whether the path is tracked. They refuse when `private/` is not covered by a rule or is tracked, so a first write cannot expose private files. An already-tracked `project.json` does not block settings writes; it is reported as a warning.

Onboarding makes sure both entries are ignored and warns when `project.json` is already tracked. Where it records them is decided in section 9.

## 5. Settings file

```json
{
  "schema_version": 1,
  "revision": 3,
  "features": {
    "workstreams": {"enabled": true},
    "smart_routing": {"enabled": true, "values": {"margin_bp": 500}}
  }
}
```

- Plain JSON read with the standard library. Duplicate keys, wrong types, unknown features, unknown keys, unsupported versions and scopes a feature does not allow are errors, reported by `features status` with the file path.
- **Absent and invalid differ.** A missing file or entry inherits the package default. An invalid file or entry makes the affected feature `unavailable`: its automatic behavior stays off even when its default is on, so a broken file can never switch a disabled feature back on. Entries that parse independently keep working.
- `revision` increases by one on every write. Because Git operations and hand edits can change the file without changing `revision`, writers hold `.vibe-workflow/private/settings.lock` and compare both `revision` and a content digest before an atomic replace, and recheck that the file is still the same regular file. Readers that admit work bind to the resolved-settings fingerprint, so a change between preparation and dispatch is detected.
- Because the file is committed, it is a normal repository file: no owner-only mode, no absolute paths, no secrets, no machine identifiers.
- For a consent feature that has migrated (phase 2), a committed `enabled: true` records the team's intent only: the feature stays inactive until the checkout holds a current grant, and status reports `needs_consent`. Before migration, any committed value for an `adapter` feature changes nothing and is reported as `ignored_override`.

## 6. Reading and writing

**API.** `runtime/features.py` loads manifests, reads `.vibe-workflow/settings.json` for the checkout, and returns, per feature, four separate states and their reasons:

- **desired:** what settings or the feature's own store ask for, with its source (`default`, `project`, `store`);
- **consent:** `not_required`, `current`, `stale` (its binding to enrollment, qualification or checkout no longer matches), `missing`, or `unchecked` when the store was not inspected (for example at startup); `unchecked` never counts as missing or current;
- **effective:** whether new automatic behavior may start now;
- **observed:** whether execution was ever observed, when the feature records it, otherwise `unknown`.

Reason codes are fixed: `default`, `project`, `store`, `ignored_override`, `needs_consent`, `stale_consent`, `prerequisite_missing`, `invalid_settings`, `adapter_unavailable`, `unchecked`. Saved consent never counts as current eligibility on its own. Shared persistence primitives (bounded reads, no links, atomic replace, revision compare-and-set, directory fsync) move into one module and replace the duplicates as features migrate.

**CLI.**

```text
workflow.py features status [<id>] --cwd <checkout> [--json | --summary]
workflow.py features enable|disable <id> --cwd <checkout>
workflow.py features set|unset <id> <key> [<json-value>] --cwd <checkout>
```

`status` is read-only and offline. `enable`, `disable`, `set` and `unset` write project settings only, and only for `control: settings` features. For the others they refuse and point to the feature's management skill until that feature migrates. In phase 1 only `workstreams` is a `settings` feature.

**Adapters.** Features whose state still lives in their own store (OpenRouter, Observability, Usage Routing, Smart Routing) get a read-only adapter so `features status` reports them. Their stores stay authoritative until their migration.

**Startup.** For enrolled checkouts, startup appends one line within its existing time and output budgets (`clients/startup.md`, `runtime/startup.py`), for example `Features: workstreams on · thread_naming on · others: features status`. It reads only the manifests and the settings file. It never reads runtime-backed stores (ledgers, databases, state directories), so `adapter` features appear as unchecked. It creates nothing, calls no network, and on any failure leaves the existing orientation, including qualification-only output, unchanged.

**Clients without startup hooks.** Project instructions written by onboarding tell agents to run `features status` before work that depends on a feature. If it cannot run, ordinary work continues and dependent automatic behavior stays off.

## 7. Migration

`features migrate --plan` lists what would move and from where, with source hashes; `--apply` performs it with private preimages and readback, one feature at a time. Reads never migrate. Legacy stores do not remain as fallbacks.

| Feature | Source | Phase |
|---|---|---|
| `workstreams` | The `AGENTS.md` block becomes `features.workstreams.enabled`; the block is replaced by the instruction from section 6. New ordinary admissions (delegation sends and `routing_settings.workstreams_enabled`) read the resolved feature. The runtime file's top-level `enabled` is kept for historical requests and their pins, and stops deciding new ordinary admissions. Explicit delegation, required review and existing ownership are unchanged | 1 |
| `workstreams.resource_admission`, `workstreams.capacity_recovery` | Where advanced runtime state already exists, phase 1 writes their current effective values explicitly into the runtime file's `features.*`; it never creates runtime state for this, because absent entries inherit the top-level flag today (`runtime/workstreams.py:99-103`), and reports them through an adapter. Phase 2 moves them | 1 (materialize), 2 (move) |
| `smart_routing` | `~/.local/state/vibe-workflow/routing/settings.json` (per OS user) becomes project settings; the API spending toggle becomes a grant, never a shared value | 2 |
| `openrouter.*`, `observability.langfuse`, `usage_routing` | Their private stores become `private/grants/<id>.json` with the same checkout binding; renamed roots drop `vcw` | 2, one per change |

Consent never moves silently. A migrated grant keeps its original binding (physical checkout, enrollment, qualification, destination, limits) or the user confirms again. Disable gets a recovery path that works when settings or databases are broken: a revocation record under `private/revocations/` blocks new admissions first, then cleanup reports what remains. Today this does not hold everywhere: capacity recovery can refuse before changing its preference, and Usage Routing disable needs a valid ledger.

## 8. Not standardized

Secrets (keychain or environment), accounting ledgers and reservations, task records and evidence, title identity and history, caches, onboarding journals and native client permissions. These are operational state, not settings; their configuration becomes visible through `features status`.

## 9. Risks and open questions

- **Where onboarding records the ignore entries (decision needed).** Onboarding's change machinery refuses `.git/` targets (`runtime/changes.py:77-81`), and linked worktrees share one exclude file, so `.git/info/exclude` would need a new, separately journaled Git-metadata operation. The alternative is the project's `.gitignore`: an ordinary recoverable project-file change that protects every clone, which suits a folder that now holds a committed file next to private ones.

- **Old and new writers at once.** During a migration, a running old package could write a legacy store. Each migration quiesces affected writers first and tests that path.
- **Inheritance of permission.** Tests must show a committed file, a cloned checkout and a copied grant cannot activate a consent feature.
- **"Enabled" is not "working."** Status keeps desired, effective and observed execution separate.
- **Scope of `workstreams` once layers return.** Automatic delegation spends the runner's capacity; a future checkout layer would let a person turn it off for themselves without changing the team value.
- **Defaults for existing features** (proposed): `workstreams` off, `thread_naming` on (`fixed`), `smart_routing` on, `usage_routing` off, `openrouter.*` off, `observability.langfuse` off. These match today's behavior.
- **This repository's Workstreams value.** Its block was removed in `502896f`, so migration has nothing to import; the user chooses the value written to `.vibe-workflow/settings.json`.
