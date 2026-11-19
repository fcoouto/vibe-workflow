# Local extraction record

## Decisions

On 19 November 2026 the user approved extracting only the tested OpenCode advisory
Smart Router and its required dependencies. The deliverable is a local,
install-ready package, not an installation or routing activation.

The approved design and plan in `docs/superpowers/` describe that local phase.
It did not authorize a push, PR, merge, release archive or new inference trial.
The separate 0.4.0 release decision authorizes replacing the legacy toolkit on
`main` through a PR and publishing the slim OpenCode package. Legacy reference
files stay local; existing Git history is unchanged.
The earlier trial's eight authorized executions are exhausted.

Implementation runs on `feat/smart-router-extraction` in the native task worktree.
The user explicitly authorized checking out the verified extraction branch at
the destination primary afterward. Local `main` keeps tracking `origin/main`;
the checkout operation is not a merge. The separate old repository, its source
changes, worktrees, history and private evidence remain intact.

The old primary's upstream tracking was removed. Its routing-list entry is to be
retired only through the accepted old package's supported command at the current
revision. Keep old state, qualifications, ledgers, caches and credentials.

## Evidence boundary

Synthetic tests cover classification, ranking, account continuity, idle meters,
positive exhaustion, private storage, CLI errors, plugin coexistence and export.
Package validation does not prove native model execution, billing or production
qualification. Real process-group termination could not be qualified because the
tool environment denied group signaling; no alternate permission path was used.

The task ledger retains exact commands, results, review and remaining limits.
Installation and activation have not occurred. Future installation uses the
README's opt-in instructions and existing native credentials without copying them.

## Extraction verification

The independent behavior/security review of candidate `f4233f1` found two Important
selection issues: missing account-wide Claude exhaustion filtering, and
overflow-ineligible rows consuming the bounded shortlist. Both received failing
regression tests before fixes. The corrected suite passes 36 Python tests;
plugin tests and final export validation are recorded separately in the task ledger.
No Critical finding or separate Minor finding was substantiated.

The single credential-free discovery smoke on OpenCode v2.2.6 exited successfully
but returned an empty plugin inventory. This does not establish native plugin
loading. No inference or global registration occurred. Mid-acquisition cancellation
and real process-group termination remain unqualified; synthetic and pre-aborted
tool checks are narrower evidence.

The accepted old package's supported routing command removed only the old
primary's list entry at settings revision 3. Readback confirmed revision 4,
unmanaged status and preservation of the other list entries. The command wrapper
expected `ok` instead of `updated` and stopped after the successful mutation.
Readback reconciled it without repeating the mutation. A whole-state before/after
hash record was not persisted, so no cryptographic preservation claim is made.

## Follow-up verification

The user requested the canonical distribution directory `dist/vibe-workflow`
and authorized the three missing loading/lifecycle checks. All three passed
without product runtime changes; see [native verification](native-verification.md)
for commands, evidence boundaries and remaining limits. These results supersede
the earlier unqualified loading/group-cleanup/cancellation statements only within
that tested scope. No permanent installation or routing activation occurred.

The user subsequently requested removing the extra directory level. The current
installable package root is `dist/`, not `dist/vibe-workflow/`. Packaging behavior
is unchanged; current export commands now target `dist` directly.
