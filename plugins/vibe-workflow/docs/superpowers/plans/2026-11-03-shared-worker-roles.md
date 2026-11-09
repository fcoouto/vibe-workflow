# Shared worker roles implementation plan

**Goal:** carry explicit workflow roles and custom role names to optional Habblaud
visibility for ordinary/managed native and OpenRouter workers, without changing
execution authority, settings or identity.

**Architecture:** one versioned metadata validator, label renderer and optional
transport adapter. Native contexts use independently observed native identities;
OpenRouter contexts keep their external display identity and real supervisor.
Store declarations in existing assignment/run contexts, not a new registry.

**Baseline:** main `499512e0d332be4c6e65c680ca8ec4aaa3adca41`, package `0.1.3`.
The user approved implementation of the all-worker requirement from the bounded
D6 pilot plan. That earlier uncommitted plan remains untouched in its worktree.
No installation, external inference or live qualification is authorized.

## Constraints and decisions

- Preserve canonical role and specialty separately from optional custom role
  name. Render the full custom name when supplied; otherwise retain `Role · Model`.
- Keep D6 authority roles, immutable task names, pins, digests, ownership, review
  independence and limits unchanged. Optional metadata participates in the
  original assignment digest; absent legacy metadata remains absent.
- Ordinary native contexts require no enrollment or runtime database. Observe
  actual native parent/child lineage before publishing. Continuations preserve
  identity; genuine fallback/new attempts use their own observed identities.
- Freeze declaration before first display binding. Replays preserve it. Reject
  conflicting existing bindings; the receiver has no supported role override.
- Use existing loopback-only transport; no native logs, invented native child,
  raw prompt/source forwarding, route enablement or model/provider/auth changes.
- Visibility failure changes no execution or acceptance result. Unsupported
  rendering and first-binding races remain explicit and unqualified.

## Tasks

1. [x] Add a real OpenRouter custom-name regression; confirm current preparation
   rejects it. Add shared metadata/transport tests for canonical/custom labels,
   Unicode/bounds, first binding, replay, conflict, delivery failure and identities.
2. [x] Implement `runtime/worker_visibility.py` and a database-free native helper
   in `scripts/worker_visibility.py`. Native observation belongs in the existing
   Codex adapter. Test real boundary behavior with in-memory native/transport
   fixtures; no live client or Habblaud connection.
3. [x] Forward optional metadata through D6/D5 preparation, replies and same-child
   continuation. Reuse the shared publisher in OpenRouter; retain legacy briefs,
   external identities and launch/verification semantics. Test replay rejection,
   read-only reviewer authority, identity preservation and failed launch.
4. [x] Author one local visibility contract and load it from native delegation
   and OpenRouter paths. Refresh the instruction map at authoring time. Validate
   package, affected suites, broader tests and representative loading paths.
5. [x] Obtain one fresh independent whole-change review, fix blocking findings
   with regression proof, and record actual validation and remaining limits.

## Review focus

Check metadata cannot acquire authority; old records remain readable; a custom
label never replaces a task/child identity; replay/fallback cannot duplicate a
native character; and transport/first-binding failures cannot prevent ordinary
work. No commit, publication, installation or live qualification is part of this
request. Preserve original WIP and the original historical handoff.

## Source validation

The final checkout passed 88 selected tests: 76 native, package and instruction
checks; seven OpenRouter cases; and five managed delegation/review cases. Earlier
broader runs passed 95 D6 and 32 Workstreams cases. Three OpenRouter fixtures
initially raced prompt delivery; their input handshakes were corrected and all
three passed on the final checkout. This was not a repository-wide test run.

Independent review found and closed defects in native spawn correlation, full
label bounds, optional delivery isolation, private path checks, receiver reset
and receipt preservation. Source validation is complete. Existing WIP and
installed packages were preserved. No installation or live qualification ran.

## Live qualification against main f16e0cf

One read-only Desktop worker ran with inherited settings. The observer verified
its actual parent, child, checkout and creation marker using Codex CLI 0.165.0.
Habblaud 1.0.3 had already registered the child as `Default`. Publication of the
declared Unicode label returned `habblaud_binding_mismatch` and left that binding
intact. This qualifies native identity correlation and conflict handling. Exact
custom label rendering on the active receiver remains a merge gate.

The primary checkout reported OpenRouter unconfigured and disabled. The feature
checkout had no eligible saved scope. OpenCode 1.19.5 was present. Live external
qualification requires an eligible checkout with explicit current worker consent
and a separately bounded canary. The retained prior canary approval is spent.

## Desktop hook canary against main 9562f1d

Codex's bundled observer listed the exact staged user hook as trusted and
enabled. One fresh read-only child in the existing Desktop chat completed.
Native metadata matched the declared parent, checkout and task name. Habblaud
1.0.3 displayed `Subagente (Codex)`, and the private context contained no
hook binding. Hook execution and metadata availability at start remain
unverified for that chat.

An offline replay with the same observed child and an isolated context copy
produced `Canary scout · 東京 · gpt-6.1-sol`. This checks correlation after
metadata is available. It leaves the live first badge gate open. A Desktop
chat that loads the changed hook is needed for the next bounded attempt.

## Fresh Desktop receiver qualification

A fresh Desktop chat on the PR worktree ran one read-only native child with
inherited settings. The staged start hook bound the declared context to the
observed child. The first Habblaud 1.0.3 snapshot after dispatch, revision 12773,
showed `Canary scout · 東京 · gpt-6.1-sol` under the actual parent. An independent
read confirmed the same child, parent, checkout, creation marker and configured
model through the bundled Codex 0.166.0-alpha.5.1 observer. This qualifies the
receiver badge for that label. Visual rendering remains unobserved.

The user approved OpenRouter worker consent version 2 on the primary checkout
and one read-only run with the two inspected fixtures, four steps and 120 seconds.
The primary checkout reports configured enrollment without accepted managed
authority or a schema-3 runtime. The supported OpenRouter configuration path
requires both. Consent remains unsaved and the canary allocation remains unused.

## Primary OpenRouter receiver qualification

The primary checkout used the workers-only basic consent path with saved
consent version 2 and revision 2. The run used the approved two copied files
with a four-step ceiling and a 120-second deadline. The actual OpenCode session
read both paths, used three steps, and returned the independently recomputed
total of 38.75. The response included one fenced JSON object and explanatory
prose. This verifies the findings and worker display without establishing
strict JSON-only output.

The Habblaud snapshot showed `Order rules scout · GLM 5.3 Flash` under the
actual coordinator. The retained result was accepted after local process stop.
Reported cost was $0.0002142948. The temporary primary input copies were
removed after hash verification, and the primary worktree status matched its
original snapshot.

The display convention was refined after this run. New external badges use the
declared role and model display name, so a new Scout would display
`Scout · GLM 5.3 Flash`. The recorded live badge predates this change. The
original receipt stays bound to its observed label. The updated badge has
software regression coverage, with live confirmation pending.
