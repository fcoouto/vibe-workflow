# OpenCode Escalation Gate qualification — 13 November 2026

## Outcome

**Original campaign: partial qualification of released 0.1.39 on OpenCode 2.2.6.** Six native
mechanical cases and both instrumented lifecycle instances passed. Interactive
TUI qualification remains blocked. No product code, gate threshold, dependency,
coordinator route, global configuration or billing route changed.

**Later user-authorized standalone TUI follow-up passed the missing selected
surface checks: actual presentation, native child identity and event ordering.**
It used 19/24 requests under a separate 15-minute allocation. The original
campaign and its failures remain unchanged. See [the follow-up](#standalone-tui-follow-up).

The [sanitized evidence](../../tests/fixtures/opencode_workflow/qualification-2026-11-13.json)
retains ordered observations, runtime hashes, request counts and explicit limits.
The [earlier report](2026-11-12-opencode-escalation-gate.md) retains its original
failures; this campaign does not replace that history or qualify the semantic judge.

## Subject and method

- Base: `1f53c2a22ff006ddab2afb6efa61c5d67258adf9`.
- Test candidate: `67e12a41fe64a49a4c62e6459e5fb7fd651fcbf4`.
- Released `index.ts`: SHA-256 `527816deab2ddfe7b470d213be2a7586e23200647bf520a6214fe9f089b3acf9`.
- Released `gate.ts`: SHA-256 `97513e84d2eefdbfe139609ef0a05934b6fd4f55bbfefb7aa8a3d20dfdcce860`.
- Native sessions used existing OAuth routes. Test-local route/effort changes
  exercised ordering without changing coordinator settings.
- Disposable repositories allowed native edits only to declared canaries. Test
  sources, fixture configuration and evaluators were outside agent write scope.
- An observer recorded actual model, permission, tool and completion events.
  Assertions correlated gate denials with native tool errors and checked bytes.
- Normal cases loaded released runtime unchanged. The two fault cases explicitly
  injected synthetic hook inputs into native plugin instances; they are not
  uninstrumented end-to-end behavior evidence.

## Native cases

| Case | Requests / share | Result and observed boundary |
| --- | ---: | --- |
| Masked failures and diagnosis | 20 / 30 | Passed. Genuine piped unittest failures exited zero; root and same-level child edits were denied. Same-level completion preserved the hold. A fresh stronger read-only child released it; repair to 99 passed. |
| Unexpected successes | 9 / 12 | Passed. Three genuine masked unexpected-success runs held the canary; denied edit preserved value 3. |
| Negative controls | 14 / 24 | Passed. Expected failures, historical output, option-bearing wrapper and mixed viewer/check remained unheld. Ordinary fail/fix/pass completed. |
| Full lifecycle | 80 / 90 | Passed after interrupted-work recovery within the original share. Three escalations, one architecture reset, three more escalations, no second reset, rejected ask preserving value 17, and one-time approval changing it to 99. |
| Lineage, isolation and resets | 18 / 28 | Passed. Independent root could edit the same path. Old child did not release. Child, negated and synthetic reset requests did not clear holds. Named root reset released only its file; general root reset cleared the remainder. |
| Reload and configuration | 14 / 16 | Passed for the stated observations. Reload lost memory without resetting request accounting. Disabled and malformed settings registered no status tool and allowed edits. Replacement patterns excluded default unittest checks after load. Positive execution of the replacement command was not tested. |
| Interactive TUI | 0 / 24 | Blocked. Two zero-byte captures coincided with shared-server restarts; no TUI behavior passed. Cause is unproven. No identical startup retry is permitted by this campaign. |
| Instrumented active-stream fault | 0 / 8 | Passed. Synthetic hold reached the native permission boundary. Injected hook error allowed the action and reported its error; stream loss cleared state and prevented reactivation. |
| Instrumented deferred-stream fault | 0 / 8 | Passed. Stream loss before deferred settings resolution stayed fail-open after resolution. Cleanup prevented later synthetic inputs from reactivating the gate. |

Total live usage: **155 / 240 model requests**, including children and auxiliary
requests. Original 60-minute deadline and per-case shares survived both restarts.
Twenty-six interrupted lifecycle requests remained charged to its 90-request
share. Completed cases were not replayed. One interrupted child was confirmed
inactive after restart without being labelled successfully completed.

The full lifecycle was checked against raw permission records: two stop-stage
asks carried correlated tool identities, followed by `reject` and `once` replies.
Eight successful recovery diagnosis/architecture children made no edit requests.
Stage snapshots showed escalation counts 1, 2, 3; after the single architecture
reset, 1, 2, 3 again. No user-message reset occurred in that case.

## Evaluator failures and corrections

These are preserved failures, not gate defects silently counted as passes:

1. **Empty protection receipts.** The evaluator excluded the absolute `/private`
   ancestor, so original hash receipts contained no files. A focused test failed
   before the fixture-relative path fix and passed afterward. All five evaluator
   self-tests passed. Explicit comparisons then revalidated each of nine fixtures
   against declared test sources, permissions, plugin configuration and actual
   tool records, producing nine protected-file hashes per fixture. A changed
   disposable test expectation was rejected. Original empty receipts were retained;
   their `protected_inputs_unchanged=true` claims are not acceptance evidence.
2. **Stale source in the reset case.** The independent root changed the canary to
   7, but the main session attempted a patch expecting 3. That source-guard failure
   remained recorded. A fresh read preceded only the unfinished released-file edit
   to 8; the other held file stayed 3 and had already received a gate denial.
3. **Configuration evaluator KeyError.** Disable succeeded, then receipt writing
   failed because the settings hash was absent. Disable was not repeated. The
   remaining reload/replacement/malformed-input observations used corrected
   protection checks. Final malformed fixture bytes were declared test input,
   not a corrupted user setting.
4. **TUI interruptions.** Both startup attempts remain recorded as interrupted,
   not passed or harmless. Explicit-server mode is documented but unqualified
   here. No service restart, database edit, credential copy or global workaround
   was used to force acceptance.

## Deterministic checks

Seven added adversarial tests bring the gate suites from 70 to **77 passing
Node tests**, with no skips. Coverage includes independent-root isolation,
stronger-than-every-level release, mixed-file decisions and read-only status,
bounded tree/file state, malformed failure counts and **10,000 fixed-seed
interleaving steps**. The Python wrapper includes all three Node suites.

Three intentional mutations in disposable copies were rejected by assertions:
cross-root state reuse, comparing only the latest repair level, and accepting
partial failure-count tokens. Released runtime and task runtime bytes stayed
unchanged.

Reproduce deterministic package checks from the task checkout:

```sh
python3 -B scripts/validate.py
python3 -B scripts/test_sharded.py test_opencode_workflow
node --test tests/opencode_workflow/gate.test.ts tests/opencode_workflow/plugin.test.ts tests/opencode_workflow/adversarial.test.ts
```

Package validation passed, with the existing bundle-size warning (at least 75%
of the 8 MiB scan limit). It does not establish native execution. The committed
candidate's focused Python wrapper passed two tests, including the Node suites.

The first unchanged-candidate full run executed **4,699 Python tests**, four
skipped, and failed one subscription-freshness test outside gate code. Its module
passed all 16 tests in isolation. The same failure was reproduced on unchanged
base `1f53c2a2` by advancing one clock second; a frozen-clock control passed.
No assertion, skip, product code or test-clock behavior was changed to hide it.
The unchanged-candidate six-worker full rerun **passed**: 206 modules, 231 units,
4,699 tests, four skipped, 1287.2 seconds, on `67e12a4`. Command:

```sh
env TMPDIR=<approved-opencode-temp> /usr/bin/caffeinate -i python3 -B scripts/test_sharded.py --workers 6
```

The runner stopped and retained output matched the completion notification. The
later documentation/evidence commit is not the full-suite candidate; its package
validation and affected checks must be reported separately. The earlier failed
full run remains part of this record.

## Independent review

One parent-dispatched native General reviewer examined the exact test candidate,
prospective report and sanitized fixture, connected runtime, raw observations,
evaluator corrections, protection receipts and mutation results. The reviewer
independently passed all 77 Node tests and five evaluator self-tests. No defect
or security finding was substantiated in that scope. Test/evidence-only security
impact was assessed as low; raw traces remain private.

The reviewer accepted publication of honest partial evidence, **not completion
of the requested campaign**. TUI proof and positive replacement-command coverage
remain missing. The full rerun was pending at that review snapshot; its outcome
above was checked separately by the coordinator. Review supplies no authority
to treat those missing observations as passed.

## Remaining limits and next action

This campaign proves mechanical behavior only. It does not prove good diagnoses,
changed approaches, autonomous architecture selection, semantic same-problem
classification, interactive TUI ordering/rendering, or OS-level write isolation.
Child permission identity was observed in API-created native child sessions;
that observation does not establish TUI-created child behavior.

The gate remains memory-only, fail-open and file-based, not a security boundary.
Same-file false holds, moving-file misses, shell/API bypasses, unsupported output
attribution and convincing printed footers remain documented limits. The judge
still waits for Smart Routing; this campaign adds no fallback inference route.

The original next action was to establish a safely isolated actual TUI startup, then complete only that
missing surface under a separately authorized attempt if the original deadline
has expired. Keep this campaign's aggregate partial until those observations
exist. Review and publication of honest partial evidence do not qualify that
missing surface or make the entire requested qualification complete.

## Standalone TUI follow-up

The user subsequently authorized proceeding with actual terminal presentation,
TUI-created child identity and event ordering, and requested an instruction
refresh from installed 0.1.39 first. The installed manifest and applicable
instructions were checked. Runtime hashes still matched the released subject.
This follow-up did not reset the expired original campaign's limits.

The CLI and private standalone server reported **2.2.6**; the shared server
reported **2.2.7**. Explicit shared-server attachment required a separate
password context, so no credential was copied or changed. Documented
`--standalone` mode instead rendered a real full-screen TUI on a controlling
pseudoterminal. The shared server's identity remained unchanged throughout.

The initial TUI-created root stopped before tools or edits after reporting
the status tool absent. A zero-inference native context inspection showed it
registered but hidden by the fixture's deny-by-default permissions. Adding
only an allow rule for the read-only `escalation_gate_status` tool restored
model visibility; file-write permissions, gate code and global settings stayed
unchanged. A second zero-inference inspection confirmed visibility. Continuation
reused the same root, two consumed requests and original deadline, executing
only previously unexecuted gate steps. The initial stop remains recorded.

Observed results, corroborated by native events, terminal output and file hashes:

- Three actual unittest checks failed on values 1, 2 and 3 against immutable 99.
  Native metadata and structured output both reported exit 1.
- Status showed `canary.py` held at two rounds, stage `escalating`.
- The parent's next patch was denied, with the gate's refusal rendered in the TUI.
- The real native `subagent` tool created one child without a model override.
  Its edit permission carried that child's identity and was denied by the same
  hold. Tool errors were correlated with permission source IDs.
- Child creation arrived at raw observer index 49, its prompt at 50. This is
  the reverse of the earlier CLI trace; the adapter must handle both orders.
- Both root and child actually used the same primary route. Child completion
  preserved the hold. Final canary bytes stayed `VALUE = 3\n`.
- The unrelated `free.py` edit succeeded with `OK = True\n`.
- Root and child completion events preceded shutdown. The owned frontend was
  stopped and reaped; protected inputs matched the declared fixture transition.

Total follow-up usage was **19/24 requests**: 18 primary and one title request,
all before the original follow-up deadline. This is separate from 155/240 in
the original campaign. Terminal capture contained 255,567 bytes; the
[sanitized follow-up evidence](../../tests/fixtures/opencode_workflow/tui-followup-2026-11-13.json)
binds ordered observations, terminal/event-log hashes and protected-file hashes.

The first evaluator incorrectly applied masked-output footer placement to
nonzero TUI results, whose content appends `Exited with code 1`. A focused
regression failed before correction and passed afterward, with three negative
controls. Acceptance now checks the actual runner output, matching native exit
metadata and literal assertion mismatches. No gate behavior was changed or replayed.

**Limits:** terminal output was inspected as ANSI/text, not pixel certification.
The exit shortcut and SIGTERM did not finish the owned frontend; forced shutdown
was required and remains an explicit harness/exit-control limitation. This
does not qualify interactive attachment to the shared 2.2.7 server, every
terminal implementation, or a TUI stop-stage approval dialog. The earlier
native release/approval evidence retains its original scope. Positive custom
check execution, semantic judging and general workflow improvement remain
unproven; a scripted mechanical exercise is not a productivity comparison.
