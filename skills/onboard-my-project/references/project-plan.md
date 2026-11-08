# Project-aware private plans

Load this only for inspect input with `project_scope` or an existing schema-2 private plan. Read the parent [procedure](procedure.md) for common contracts, source acquisition, coverage, conflict handling and reporting; do not duplicate that workflow. A private Project plan is not an enrollment record. The bare JSON D4 entry rejects it before transaction validation, worktree locks or journal creation. Canonical application requires the verified coordinator owner and scoped operation in [apply/recovery](apply-and-recovery.md#canonical-operations); inspection alone grants no startup or runtime adoption.

## Explicit inspection scope

Keep the inspect source object's `schema_version:1`, clients, Toolkit manifest and required sources from the parent procedure. Add exactly:

```text
project_scope = {
  kind, project_root, member_checkouts,
  membership: {include, exclude, future_join, authorization},
  authority: {path, sha256, reference},
  project_files
}
```

`kind` is `standalone` or `umbrella`. Select canonical absolute Project/member roots. A standalone `project_root` must match its observed primary Repository root and `member_checkouts` must contain exactly one selected checkout; that checkout may be a linked worktree and remains the action base. An umbrella requires a non-Git Project root. `authority.path` must be exactly `<project_root>/.vibe-workflow/project.json`. Membership uses literal root-relative include/exclude subtrees (`.` is the root boundary), with exclusions taking precedence; `future_join` is boolean and `authorization` records its scoped reference. Authority also records the expected SHA-256 or `absent` and decision reference. These declarations do not authenticate authority or adopt a member. Unsupported authority records, conflicts and uncertainty remain gaps. Standalone `project_files` is empty; an umbrella names its finite shared files.

Keep private storage outside the Project, every selected checkout, plugin caches and instruction discovery. The helper strips `project_scope` before source validation and retains source material, requirements, pins and history once in the existing `plan.json`. It creates private plan schema 2; it does not create schema-2 enrollment.

Each Repository owner binds `kind`, `project_id`, `repository_id`, `checkout_id`, selected `root`, `primary_root`, `git_dir` and `git_common_dir`. Keep the selected root as the snapshot/action base. Primary/common-Git worktree resolution locates membership/shared guidance; it does not switch action checkout. Initial inspection rejects duplicate common-Git Repository selections rather than implying multi-checkout execution. Project owners bind `kind`, `project_id` and `root`, with named-file observations only. Member trees, Git metadata, links and escaping boundaries cannot become Project-owned targets.

For one exact, nonconflicting canonical authority, `id_origin:canonical_binding` reuses its Project ID and existing Repository/checkout IDs only after matching the complete recorded authority, owner and physical identity bindings. A supported legacy record may retain its current ID as `id_origin:legacy_binding`; newly generated IDs remain proposed. Neither source authenticates the original human decision or proves global uniqueness. Bounded discovery cannot recognize an undisclosed copy/move; reconcile observed conflicts instead of guessing from paths/remotes. Shared instructions do not copy repository preferences or establish actual hook loading. Record software root, actual hook/harness source, command cwd, guide locator and relative-path bases distinctly.

## Scoped proposal input

For schema 2, `onboard plan` accepts exactly `expected_revision`, `assessments`, and optional `reconcile:{input_digest,reason}`. `assessments` is `{project, repositories}`: `project` is the shared-file assessment or null for standalone; `repositories` lists every selected Repository in frozen order. Retain each assessment's exact frozen `owner` and `identity`; author only its existing `inventory`, `decisions`, `coverage`, `actions`, `evidence` and `semantic_assessment` slots. Do not hand-edit helper-retained target observations.

Every Repository retains the common procedure's complete software inventory, nine-document mapping, full pinned catalog/policies and native-evaluation plan. A non-Git Project inventory contains only relevant existing sections/documents, has empty coverage, and uses semantic flags `complete`, `conflicts_checked`, `instructions_checked`, `source_provenance_checked`. It makes no software-adoption claim. Reference every Project action from coverage in every selected Repository; the current helper conservatively treats all selected members as affected. Missing required Repository coverage cannot move into the empty Project matrix.

Common row fields retain their meanings, with two action additions:

- `owner` is exactly the complete frozen assessment owner, including Repository/checkout IDs and selected-root/Git bindings where applicable. Do not add parallel identity declarations or substitute `primary_root`.
- `cwd` is null except for commands, where it is the canonical absolute actual command directory under that owner's selected root. D3 records commands; it executes none. Reuse existing check/command mechanisms.

File targets remain relative to `owner.root`. Repository file actions use `scope:checkout`; Project file actions use `scope:shared`. Preserve exact before/after fingerprints, complete postimages, preservation exceptions, authority, dependencies and backup requirements from the common procedure.

All schema-2 action/evidence/decision references, including local references, use:

```text
project/<project_id>/<local_id>
repository/<repository_id>/<checkout_id>/<local_id>
```

Escape each identifier segment as JSON Pointer text: `~` → `~0`, `/` → `~1`. Local IDs may repeat between owners; resolve references in their expected record collection, never by guessing a bare ID. Coverage requirement IDs/dependencies and local evidence-input names keep their existing meanings. A Repository may reference shared Project configuration/authorization evidence; sibling Repository evidence and actions cannot substitute for its own adoption. Behavior, baseline and observation proof stays local to its actual owner/current inputs. Shared actions or a sibling receipt cannot qualify Repository behavior. Whole-plan dependencies and each owner's declared action order must be acyclic.

Each schema-2 evidence row adds `input_digest`, computed after assessing the recorded inputs. Use the Repository's frozen `assessment.identity.snapshot`, or the Project's `assessment.identity`, as `identity`. Build `values` from its `fingerprints`; add present `checkout:<key>` entries for `repository_root`, `git_common_dir`, `head`, `index_digest`, `dirty_inventory`, `paths`; add saved `source:<path>` observations and `package` pins. The payload is:

```text
{"owner": <complete frozen owner>,
 "inputs": {<each declared input name>: <values.get(name)>}}
```

For Repository `behavior` or `baseline`, add this `context` field:

```text
{"checkout": <complete frozen selected-checkout snapshot>,
 "project_files": <all frozen Project named-file fingerprints, or {}>,
 "package_pins": <saved package pins>,
 "sources": <saved source observations>}
```

Hash `json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()` with SHA-256. A newly named, unobserved target contributes null, not proof; record its observation and reassess before using fresh evidence. Behavior/baseline needs an actual local file or checkout input; the Project shared-file owner cannot assert these kinds. After exact reconciliation, use the reassessed current observations/pins. Unchanged old receipts stay blocked when their bindings differ, including changed shared files. A digest binds a claim; it does not authenticate behavior or authorization. Schema 1 is unchanged.

## Targets, freshness and completion limits

Before review-ready, inspect proposed named targets/absence through bounded owner observations. New Project names can extend named-file observations without changing frozen membership/scope; they cannot conceal member-tree or Git-metadata edits. Repository targets extend fingerprints/instruction inspection on the selected checkout. Helper-retained `identity.targets` observations bind canonical location, parent chain, file identity/absence and content fingerprint; command cwd retains directory identity.

Resolve targets across the entire plan. Reject canonical/physical overlaps, ancestor conflicts and aliases even with equal postimages, plus uncertain target resolution. Each physical target has one declared owner/postimage. Input/review digests retain exact owner/checkout/base/target observations for later D4 application and recovery.

Reinspect before and after proposal validation. Keep current observations separate until the exact current-input digest is explicitly reconciled. Preserve qualified decision IDs/dispositions/history, stale invalidations and expected revisions. Changed source bytes cannot be reconciled to the old pin; reconciliation cannot rebind Project/Repository/checkout identities, roots, membership, scope or authority. A valid saved plan without historical authority-parent observations remains readable with that proof absent in both frozen and current records, and reports `authority_parent_observations` stale until a newly reviewed plan records the parent chain. Apply existing aggregate request/plan/file/byte/path/row limits across all owners; incomplete bounded observations cannot count as full coverage.

Status precedence remains stale inputs, pending decisions, blockers, incomplete draft, then review-ready only after complete scoped and whole-plan checks. Structural checks and authored semantic flags do not prove native execution. Review-ready grants no application, enrollment, task readiness or merge authority. Load [apply/recovery](apply-and-recovery.md) only when that operation is requested; the verified canonical operation boundary remains mandatory.
