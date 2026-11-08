# Workstream-session terminology validation

## Scope

The user's 8 November 2026 clarification separates a dedicated, outcome-owning
**workstream session** from a **subagent** invoked for bounded assistance. The
approved naming revision uses **COORD**, **STREAM** and **AGENT** for new contexts,
while existing P/C presentation records retain their identities and lifecycle.
The [source map](policy-sources.md#workstream-sessions-and-subagents--8-november-2026)
records the preserved Toolkit requirements and the user's local extensions.

The implementation branch starts at `563d64b` from current `origin/main`. A separate
worktree preserves unrelated work in the original checkout. This task does not
install a package, migrate active records or introduce a session-launching service.

## Acceptance

- Entry points distinguish session-level outcome ownership from helper invocation;
  a resumable subagent is not silently substituted for a required workstream session.
- Enabled authorizes suitable workstream-session coordination inside the approved
  task. Small direct work, explicit review, unavailable-capability reporting and
  unrelated peer-chat permissions remain clear.
- Native lineage, workflow association and filesystem isolation are distinct.
  Technical protocol identifiers and historical observations remain truthful.
- New title roles work through the existing formatter and configuration path.
  Existing P/C records, manual-topic edits, paused synchronization, pending writes,
  family binding and task replacement remain protected.
- Complete local procedures remain discoverable, skill entry points stay within
  150 words, source coverage is updated, and relevant regression checks pass.

## Execution and evidence boundaries

The coordinator retained GPT-6 Astra/high. The implementation was assigned to
GPT-6.1 Terra/high through the existing OpenCode v2.2.6 native account route, with
explicit model/variant selection and unchanged session permission rules. Selection
followed the refreshed official OpenAI model-selection guidance for supplied-material
writing and scoped code changes; stronger independent review and deterministic
checks verify the result rather than assuming a smaller model is sufficient.

OpenCode's session API created a separate primary `build` session, with no native
`parentID`, in the implementation worktree and the same project as its coordinator.
The returned model/variant and location matched the request. Its workflow association
uses the existing family register and task record; this is not native parentage.
The prompt API admitted the bounded assignment. Assistant-message metadata confirmed
Terra/high execution, and a native idle wait plus message retrieval returned the
implementation handoff. The first CLI wait timed out while execution remained
active; a subsequent bounded wait completed without resending the assignment.
These observations establish this API creation/execution/retrieval path, not sidebar
rendering, arbitrary client support or package-managed session orchestration.

Read-only instruction exercises use isolated source snapshots and the same model
settings. They compare procedure loading and next-action interpretation for useful
parallel outcomes, unavailable session creation, review fixes, disabled Workstreams,
small direct work and existing manual titles. They do not create real sessions or
prove native enforcement, restart behavior, messaging or visible rendering.

## Results

### Baseline instruction interpretation

A fresh Terra/high helper read an isolated `563d64b` instruction snapshot through
the Workstreams entry point. For the two independent outcome-sized deliverables,
it selected reusable “native children” and found no instruction requiring separate
project sessions. With independent-session creation unavailable, it selected a
resumable subagent and considered that consistent with Enabled Workstreams. This
reproduces the ambiguity identified by the user; it is an observed interpretation,
not a measurement of how often agents skip the feature.

The same reader retained the intended owner for review fixes, allowed explicitly
requested independent review while Workstreams was disabled, kept a typo fix
direct, and preserved manual titles. It selected the old C label for a new helper
reviewer. These unchanged behaviors are comparison controls for the candidate.

The reader loaded the Workstreams skill/preference, relevant shared policies,
external-selection and visibility contracts, and naming/title procedures. It made
no execution or title changes.

### Candidate instruction interpretation

A separate fresh Terra/high reader received the same six scenarios and an isolated
candidate snapshot, without the baseline conclusions or expected answers. It chose
two dedicated workstream sessions for the independent deliverables, and reported
missing session capability rather than claiming a resumable subagent fulfilled it.
It retained the actual backend owner for review fixes, permitted explicitly requested
read-only subagent review while automatic workstream coordination was disabled,
kept the typo fix direct, and preserved existing manual P/C titles while identifying
the new helper reviewer as AGENT. It correctly left the exact manual-edit outcome
dependent on the saved baseline and whether the topic or title structure changed.

The relevant skill, preference, ownership, review, handoff and naming procedures were
observably read. This bounded comparison supports improved instruction interpretation;
it does not establish live dispatch or a general reliability rate.

### Package, runtime and review checks

- The implementation owner reported 106 focused tests passed after observing the
  new-label regression fail against the original formatter. Its initial instruction
  tests/package check found the stale coordinator-owned instruction map.
- The coordinator regenerated that map and ran `python3 -B scripts/validate.py`:
  passed; the Workstreams entry point is 138 words. `git diff --check` passed.
- An independent GPT-6.1 Sol/medium reviewer inspected all 33 changed/untracked
  candidate files and relevant callers, including authorization and legacy-title
  compatibility. It found no substantiated blockers or regressions, independently
  ran `python3 -B -m unittest tests.test_title_sync` (17 passed), and verified all
  72 prepared-map entries' hashes and complete line coverage.
- The review target was based on `563d64b`, with candidate-manifest SHA-256
  `0a194e585fc2d4faf950031cd06e24600fab3bb1b61020d1aa7da84403c23f44`.
  Before/after manifest and tracked-diff checks matched; the index remained unchanged.
- `python3 -B -m unittest discover -s tests` **did not complete**: it reported two
  error markers before termination by SIGTERM, without reaching the final failure
  tracebacks or summary. Their causes were not established. This is neither a
  passing full suite nor proof that the errors are unrelated to this change. The
  reviewed candidate remained byte-identical when the command ended.

### Remaining gate

The implementation and focused review are ready for a draft PR, but the branch is
**not ready to merge**. Complete and diagnose the full suite, and reconcile/recheck
against newer `main`, before marking it ready. No live rendering, installed rollout,
stored-title conversion or managed OpenCode session adapter was tested. The final
documentation-only addition records these outcomes; runtime/instruction behavior
remains the independently reviewed candidate.
