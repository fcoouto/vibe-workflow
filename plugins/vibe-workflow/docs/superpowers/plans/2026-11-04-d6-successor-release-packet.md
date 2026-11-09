# D6 cooperative pilot packet

**Completed on 5 November 2026.** This packet records one authorized standalone
Mac CLI pilot under the [D6 plan](../../d6-native-delegation-plan.md). Its live
allowance is spent. A later attempt needs its own scope and authorization.

**Purpose:** demonstrate one reserved native worker change, applicable checks,
one independent review and a verified cooperative handoff on one native surface.
The observed setup and paired child hooks apply to this exact fixture. D4
adoption, D5 READY and D7 merge gates have separate acceptance rules.
The [held managed-hook record](2026-11-03-d6-managed-lifecycle-pilot.md) preserves
that separate stronger sequence and its historical evidence.

## Completed attempt

The staged `0.1.23` package matched tested revision
`764c39b7e0f5caeaef9c48dd805f2d26754b7768`. Codex CLI `0.165.0` on macOS arm64
passed bounded bootstrap and metadata checks with five scoped trusted hooks.
The user’s coordinator settings and authentication were preserved.

- One native Luna/high worker changed only `app.py` from `value = 1` to
  `value = 42`. The nested `apply_patch` produced a matched child-bound
  PreToolUse and PostToolUse pair. The worker used no wait or marker and
  reconciled cooperatively.
- The registered builtin-only check rejected the baseline and accepted the
  exact postimage, including its single newline. The original execution was
  independently inspected and strictly reconciled.
- One separate Astra/high reviewer used no tools and reported zero findings on
  the same candidate. Runtime reconciled the reviewer and returned
  `qualification_verified: true` for candidate
  `5ebc01592bcaf858a31ef1864158c045b677bbb250c013645e4846e7728ed13f`.
  The private verification record has SHA-256
  `aa8013f501057edc514486955d07e87b4151ba017da17e3257cc1363cf0f31af`.

For a separately authorized attempt, reuse existing artifacts and record these
facts once.

| Record | Minimum needed |
| --- | --- |
| Source and route | Reviewed source revision plus any reviewed working diff, package identity, exact native surface/executable and supported dispatch/observation route. Preserve the user's account, model, reasoning and permissions. |
| Scope and ownership | One disposable public-data checkout, the existing valid authority required by the chosen route, exact worker files/resources and actual coordinator/worker/reviewer identities as observed. No conflicting or uncertain owner. |
| Work and acceptance | One small one-file fix, unchanged test, expected result and commands to check it. Reviewer sees the same candidate. Use runtime-produced identity/candidate digests where required. |
| Limits and effects | One worker and one reviewer sequentially, one review round, no retries/replacements, and a 60-minute total window. Keep required admission counters and command timeouts explicit; no unrelated configuration or services. |
| Result | Actual checks, review findings, normal-completion observations, claim disposition and limitations. Software evidence and native observations stay distinct. |

Record setup effects only if the selected route actually needs setup. Selected
protections remain prerequisites for that route; this packet does not waive them.
The original historical failed claims stay with their own authority and limits.

## Cooperative completion and held ownership

Use the [canonical completion rule](../../d6-native-delegation-plan.md#normal-completion-and-uncertain-ownership).
The required evidence concerns the actual assignment, native completion,
foreground work and inspected effects, plus coordinator control of follow-ups.
No independent observer of hidden host queues or retries is required by the
revised design. No known pending continuation may be ignored. Independent review
of the result remains required.

Use the [current version-3 proof fields](../../../clients/delegation-proofs.md#cooperative-native-completion).
Retained version-2 proof requirements are unchanged; do not fill their stronger
boolean from weaker evidence. Missing required evidence retains claims, capacity and consumed budget.
Automatic takeover remains deferred.

The offline fixture's external lifecycle proof is synthetic. The Mac pilot used
its own observed command, shell, environment, input, and actual executor result.
Project runners with plugins, subprocesses or unknown imports need their own
writer evidence.

## Sequence for a separately authorized attempt

1. Confirm the reviewed route supports the entire sequence and the chosen scope
   is available. Capture the initial candidate and a failing baseline for the
   small fix. Use existing setup only where actually required and authorized.
2. Reserve the worker scope and dispatch once through the native coordinator.
   The worker makes the bounded change and reports its result. No background
   jobs, nested workers or mutable services belong to this pilot.
3. Inspect actual native completion, foreground activity and the diff. Reconcile
   the worker under the revised cooperative contract. Stop dependent work on
   ambiguity; preserve the claim and partial edits.
4. Run the applicable check, then obtain one read-only independent review of the
   same candidate. Reconcile each operation under its actual supported contract.
   A registered D5 check still needs its own valid reconciliation.
5. Recheck candidate and ownership before accepting the handoff. Reuse unchanged
   passing evidence where allowed; rerun only if changes or the selected check
   policy require it. Record the bounded result without claiming READY or merge
   authorization. Any unresolved operation prevents a successful handoff.

For basic delegation, check the result and applicable tests. The bounded managed
canary binds the exact patch to the original child claim. The separate historical
managed-hook record retains its original marker protocol.

## Release checklist

- [x] Implement honest cooperative completion and the bounded end-to-end
  admission/check/reviewer path, preserving selected protections and strict
  consumers. Task-level offline regressions passed; final combined review and
  verification are recorded in the D6 plan.
- [x] Pass affected offline checks and package validation. The tested runtime
  passed 86 affected tests. The initial 66-case run remains historical evidence.
  Replays, conflicting claims, wrong/stale results, interruption and uncertainty
  remain offline cases; do not inject these failures into the live pilot.
- [x] Record the supported route, exact scope, check and finite limits. Review
  the bounded packet independently and collect native evidence after user release.

Optional display, other native surfaces, cross-repository work, schema migration,
D7 enforcement and D9 measurement remain separate. Earlier failed attempts retain
their original claims and artifacts.
