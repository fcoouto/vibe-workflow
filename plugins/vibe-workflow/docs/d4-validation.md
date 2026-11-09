# D4 onboarding validation

## Managed-runtime birth without machine history — 3 November 2026

Fresh explicitly selected D4 creation now records a version-2 absent-machine
witness instead of creating unrelated temporary-review storage. Version-1 ready
history keeps its original requirements. Existing authority, peer, writer,
descendant, exact-pin and recovery checks remain in place. Original certificates
are retained; later unrelated storage is accepted by retained-history/accounting
checks, while related owners still block dependent use. Reapplying an original
transaction continues to require its exact captured history.

The genuine `history=False` regression first failed at the ready-history gate.
Fourteen new cases cover creation/undo, absent and existing private parents,
malformed or missing known storage, related history, callback expiry/escape,
mutable/appearing parents, configuration appearing during checkout inspection,
and interrupted resume. Those cases plus the existing ready-history lifetime
test passed: 15 tests in 20.392 seconds.

Independent general/security review found two boundary defects. A final machine
path/parents/absence/deadline recheck closes the stale-at-issuance race. Preflight
refusal prevents a destination from creating a missing machine-configuration
ancestor and invalidating its own witness. Both findings have observed failing
regressions followed by passing fixes. A destination beneath an already-existing
private machine parent remains supported.

Final compatibility/recovery run: all 78 selected tests passed in 349.765 seconds.
The earlier run had 76 passing tests and two pre-existing startup-orientation
fixture errors, independently reproduced on unchanged main. The shared
`qualified_entry` mock now accepts and forwards the `managed` keyword, preserving
its qualifier and state/authority assertions. Both affected tests first reproduced
the errors, then passed after the fix. Independent follow-up review found no
blocking issue in that mock change. Package validation and `git diff --check`
passed. This establishes the selected source/fixture behavior, not a complete
repository-wide suite or native-client qualification.

Integration with main `c010e7f` also covers prepared instruction loading and the
updated startup/runtime paths. Regenerating the prepared map changes only the
`clients/runtime.md` entry; its two bounded pages reconstruct all 96 lines exactly.
The expanded run passed 205 of 207 cases in 537.097 seconds. Two orientation
assertions expected a period after the quoted authority path, while newer main
continues that sentence. Removing only that punctuation expectation preserves
the exact path, state/inode/mode/mtime and member-authority checks. Both affected
cases then passed in 9.211 seconds. Independent integration review confirmed that
production code and the map were unchanged, so the other 205 results remain
applicable: cumulative coverage is 207/207, not a second full-suite run.

Manual/static instruction checks cover ordinary native editing, explicit birth,
known-owner conflicts and file-only undo. Required local procedures remain
reachable without Toolkit retrieval; ordinary work gains no runtime prerequisite.
No installation, hook-trust change, live project/runtime activation or native
client qualification was performed. Primary WIP and installed runtime pins were
not changed.

## File configuration and recovery — 2 November 2026

The simplified canonical file path separates configuration, activation and undo. It reuses the existing transaction primitives and retains explicit runtime creation, active/shared-state transitions and command controls on their applicable paths. Original package/source pins remain provenance; file undo can use a newer supported reader.

Validation covered configuration without runtime birth, unrelated input and review-history growth, older recorded pins, edited/replaced outputs, corrupt backups, substituted parents, overlapping operations, live writers, missing allocated runtime, and interrupted replacement/deletion. Fourteen file-transaction tests passed. Existing-store admission and recovery checks passed after replacing the obsolete expectation that configured-only completion must return pending. Broader composition, active rollback, birth lifetime, onboarding inventory and legacy transaction checks passed. Package validation, skill validation and whitespace checks passed; the onboarding entry is 148 words.

Independent general/security review found a runtime-identity check before lock acquisition and stale activation guidance. A regression reproduced the lock gap; the fix verifies and retains exact authority under the held guard. Review accepted both fixes. Representative instruction paths distinguish file undo, configuration completion and activation with missing native proof.

The actual self-host pilot then used the verified coordinator entry and its existing rollback authorization. Transaction `self-host-20261102-configured-1` reached `rolled_back`, exit 0. Both transaction-created files (`docs/setup.md` and `.vibe-coding-workflow/project.json`) returned to absence. All 357 retained files matched their pre-operation bytes/modes; the original runtime baseline, birth certificate, approved manifest and frozen plan remained unchanged. The installed entry resolver reports both the primary and linked development checkouts as `outside / not_enrolled`.

These results prove file recovery and entry resolution. They do not establish managed activation, native client qualification or repository enforcement. Historical observations below retain their original scope.


26 October 2026. Transaction implementation, independent review and bounded disposable adoption checks are complete. **Successful full project enrollment remains unqualified:** the disposable project has no qualified repository gate or complete client-operation evidence. Native operation remains the default.

## Implementation and review

The transaction module binds the exact D3 proposal, approval reference, manifest, checkout and source/package pins. It journals file backups and outcomes, serializes each worktree, reconciles interrupted changes, preserves unrelated edits, and activates enrollment last after required observations qualify. Commands require bounded effects, recovery contracts and current evidence that the exact attempt and all writable descendants stopped. JSON bindings do not authenticate human authority or establish client behavior.

Independent Sol6.1 review found and verified fixes for five defects: surviving commands after parent death, writer checks skipped during recovery, optional blockers gating mandatory enrollment, absent parent directories, and null entries accepted as evidence. A follow-up fixed direct rollback reporting after uncertain directory creation. Scoped re-review has no outstanding findings. Unknown directory ownership is preserved and reported; unresolved command ownership blocks competing plans without guessed process kills.

| Check | Result | Scope |
| --- | --- | --- |
| Final full unit suite | 218 passed in 89.283 s | Includes 44 transaction/recovery tests and CLI integration |
| Package validator | Passed; entry points 149/143 words | Package structure, not client execution |
| Skill Creator and whitespace | Passed | Skill metadata and `git diff --check` |
| Fresh delegated resume-loading check | Passed | Recovery guide and applicable contracts loaded; D3 procedure and upstream sources stayed unloaded |
| Independent implementation and adoption review | No outstanding scoped findings | Recovery fixes and nine exact adopted document postimages |

Final runtime fingerprint: `fd8f5d15ee1f652b37b5a4b9081916d86685a474b2c79ab800df3682aaadbea6`. A coordinator test run using restrictive `umask 077` produced mode-sensitive fixture failures; the inherited environment passed without a runtime change for those failures. The pre-existing nonfatal SQLite `ResourceWarning` remains. Process-death tests exercise recovery; physical power-loss behavior was not observed.

## Disposable Toolkit adoption

The final candidate used a new credential-free Python project, preserving the original queue behavior, tests and explicit README/AGENTS customizations. It retained all 95 coverage rows and verified 96 source hashes at Toolkit `790a895304e0c08b70a5b215c9d334f1c86b816c`. This reused a trusted pinned source bundle; current upstream freshness was not checked.

Real `inspect`, `plan` and `apply` commands produced plan `c8689dbbe965413e8f503c2837d96991`, revision 2, and applied nine compatible canonical-document/instruction files. Existing tools were reused; no dependency or registration needed installation. An external evaluator verified exact postimages, document links, product/test hashes, single customizations and enrollment absence. Independent general/security review found no actionable issue in those postimages; the final fixture used identical bytes.

The two product tests passed before adoption, after adoption and after recovery. Repeating the exact apply request changed neither content nor backup names. Resume preserved the pending qualification state. After a simulated later user edit, resume and rollback preserved that edit; rollback restored every other baseline file and correctly remained `rollback_partial`. The coordinator independently checked the sources, postimages, restored baseline and preserved edit.

Enrollment never activated. The mandatory merge-target decision and repository-controlled enforcement remained blocked; missing qualification was not replaced with synthetic observations. This is native delegated instruction execution and real CLI adoption/recovery evidence. It does not establish complete onboarding, restarted-client skill discovery or future task readiness. Partial recovery artifacts remain intact for manual reconciliation of the simulated edit.

## Native startup observations

Two fresh Codex CLI 0.163.0 app-server turns exercised the unchanged candidate SessionStart hook through process-only registration. An explicitly seeded disposable enrollment received one workflow orientation entry; an unrelated checkout received none. A separate trusted stop hook ended both turns before model generation. Exact native hook trust, existing hook metadata, model/provider/effort, permissions and global configuration bytes and parsed values were preserved. All owned processes stopped.

This proves startup behavior in those fixtures. It does not prove installed-plugin discovery, desktop behavior, model compliance, delegation, D4-created enrollment or complete project adoption. The hook, dispatcher, startup module and adapter bytes still match the tested candidate after transaction fixes. The initial discovery-only attempt stopped before a turn because native hook configuration normalized optional fields; the corrected comparison retained those fields and required every unrelated configuration value to remain equal.

The existing Context7 connection also resolved Python and returned official CPython documentation for POSIX `subprocess.Popen(pass_fds=...)`. Queries contained only the public API concept. This is an observed integration reuse check, not future task readiness.

## Follow-up onboarding and recovery tests

Sol6.1 ran the execution checks; Luna independently tested instruction routing. The unchanged runtime passed 46 focused transaction/dispatcher tests in 62.175 s. The existing 218-test full-suite result remains applicable; runtime and test sources did not change.

A fresh real-Toolkit fixture retained 95 coverage rows and 96 verified source hashes. Nine compatible file changes applied while missing qualification correctly prevented enrollment. After a simulated later edit, rollback preserved that edit and reported `rollback_partial`. Explicit test-only reconciliation saved the edit outside the target and restored its known transaction postimage. Retrying the same bound rollback reached `rolled_back`; a repeat remained successful. Independent checks confirmed exact baseline bytes, modes and inventory, no new document paths, and no enrollment. The two product tests passed before adoption, after adoption and after recovery.

A separate controlled contract simulation tested the positive lifecycle through the public CLI: missing observations blocked activation; simulated qualification enabled D4 to create enrollment; a fresh native startup emitted one orientation. Public rollback then restored the baseline and removed enrollment. A second fresh startup in the same checkout emitted none. Both turns stopped before generation; native settings, existing hooks and global configuration were preserved. This connects actual D4 enrollment mechanics to native startup, but simulated qualification does not establish real Toolkit or repository-gate readiness.

Luna initially chose `resume` for `rollback_partial`. One guide sentence now explicitly routes that state to `rollback`; an independent fresh recheck selected the correct operation and preserved the edited-file boundary. Package validation passed. No runtime fix was needed.

Evidence: `/Users/fcouto/.local/share/vcw-validation/d4-testing-_myp6ei2`, with execution reports in `recovery/` and `lifecycle/`, original and corrected instruction checks in `guidance/`, and independent checks in `coordinator-verification.json`. No live project was enrolled or repository gate tested.

## Evidence and completion boundary

Private evidence: `/Users/fcouto/.local/share/vcw-validation/d4-implementation-ujrhepc9`. Final adoption is in `adoption-final/`; earlier evidence remains unchanged. Final adoption report SHA-256: `cca71c1b86098e055afa1ea9ec022035ff785d3e38b5cd8f6032b3b585d2e20c`. Native report SHA-256: `218b9d969697ab03611df6a7a08c2f20e479fc10371e5dce938c62e6642bc59f`.

Package/helper tests, delegated instruction execution and fresh native startup are separate evidence categories. Missing mandatory project mechanisms must remain pending; a temporary qualification record or `applied` result cannot establish task or merge readiness. Validation performed no live project enrollment, global installation or release publication.
