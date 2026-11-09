# D2 Codex runtime validation

25 October 2026. D2 task controls, guard, invalidation, bounded Stop handling and doctor are implemented. Native execution is qualified only for the observed local Codex CLI and bundled app-server paths below. This evidence does not establish project onboarding, general orchestration, readiness or merge enforcement.

## Package and implementation evidence

The final runtime fingerprint is `0cd704663369b21543a2e66956f58f0daeb09500cbf24b0f5bb3e66716e708af`. Root integration ran **141 tests** in **16.990 seconds**, package validation passed (skill entry point 143 words, `client_execution: not_checked`), and the final independent code/security review was clear after one diagnostic import fix and two CLI regressions.

Native matrix runs used an earlier runtime fingerprint, `d6a1d6acde9f23ddb4737947fe6312f12dd24fc72cc083aca96a41a68bb1655d`. File-hash comparison shows only `scripts/workflow.py` changed between snapshots: task API import became lazy and import failures became sanitized. The engine, state, adapters, doctor and hook definitions are byte-identical. Final-candidate direct CLI read-only and editable guard cases were rerun; bundled final-candidate read-only, editable and Stop cases were also observed. Therefore the earlier CLI failure/delay matrix is evidence for unchanged hook paths, not an identical final source snapshot. The final native doctor independently inspected the final installed copies.

## Native execution observed

Disposable local macOS/APFS repositories and private state were used. Native ChatGPT authentication, provider, saved settings, service tier, approval policy, sandbox, hooks, plugins and permissions were preserved. The authorized worker model `gpt-6.1-sol` with `high` reasoning was explicitly selected for qualification; saved default model was `gpt-6-astra`. No API key fallback, proxy, trust bypass or real-project enrollment was used. The observed CLI was Codex `0.163.0`; the bundled executable was `0.164.0-alpha.11.1`.

The earlier component matrix used process-only trust entries for the exact candidate session definitions, then an identity-only forwarder that passed native payload bytes and candidate output unchanged in ordinary scenarios. A fixture gate explicitly bound the observed startup session before work; the delayed Post case held the actual native receipt only until the coordinator performed an explicit revision-checked rebind. Final direct CLI guard cases used unwrapped runtime hook commands, with startup setup still gated. A separate unauthenticated temporary installation proved final plugin discovery, normal trust and doctor checks. No model lifecycle turn ran through that installed-plugin path, so this report does not claim installed-plugin execution.

| Behavior | CLI | Bundled app-server |
| --- | --- | --- |
| Active read-only canonical patch | Final direct candidate denied; target unchanged | Final candidate denied; target unchanged |
| Active editable patch | Final direct candidate completed; target changed | Final candidate completed; target changed; Post advanced generation |
| One explicit Stop continuation | Consumed once; native follow-up occurred; no rearm | Same bounded behavior observed |
| Correlated Interrupt pause | Correlated native Interrupt paused task and interrupted turn; no restart observed. | Controlled case passed: after the canonical read-only patch was denied and left the target unchanged, actual Stop output `{}` was held for at most 2 seconds. Persisted task status independently confirmed the current native turn and `turn_uncertain:false`; exact Interrupt completed in 82 ms and paused the task. The turn was interrupted; one turn start and no restart were observed for 2 seconds. The two earlier two-tool attempts remain unqualified. |
| Legacy and unenrolled | Patch ran; no runtime task was created | Not tested |
| Failed local tool | Native command exited 7 and produced a Post receipt; six total native tool completions, including preparation actions and patch, advanced generation 0 to 6 | Not tested |
| Missing/disabled/untrusted/modified hook; crash, timeout or malformed output | Patch ran despite read-only task; these failures did not deny | Not tested |
| Runtime database lock | Running guard denied and native patch was blocked | Not tested |
| Delayed Post after binding replacement | Observed; generation advanced and replacement task state/turn remained unchanged | Not tested |

The failure cases show that absent, untrusted, disabled, modified, crashed, timed-out or malformed hooks can permit a write. The state-aware guard denied a patch when its database was locked, but native hook infrastructure is not generally fail-closed. The canonical `apply_patch` call is the only guarded write path; shell, unmapped tools and external edits remain outside coverage.

Turn correlation is conservative. If PostToolUse for a turn arrives before its canonical PreToolUse establishes the binding, it remains unbound; a later Pre for that same turn cannot adopt the earlier event. Stop continuation and correlated pause therefore require a turn first established by canonical PreToolUse. Unknown turns suppress continuation. The bundled controlled Interrupt result qualifies only the observed Stop-pending lifecycle point; it does not change the prior read-first correlation limit or establish general workflow orchestration/read-first continuation support. The bundled failure/delayed-Post matrix was not run.

## Independent doctor observation

The final installed package was discovered and trusted normally in separate unauthenticated disposable homes for each executable. Doctor then ran against an actual disposable enrolled checkout with compatible initialized private state. Both executables returned exit 0 for package, enrollment, state and five hook registration/trust checks. State and config bytes were preserved; no database was initialized by doctor. Temporary homes were removed. Each report retained `execution.status: unverified` and `desktop_loaded_definition: unknown`: no hook or model turn was executed in this doctor check, and it does not prove the live desktop loaded the definitions.

## Timing and limits

Twenty final-candidate local dispatcher runs per case measured p95 of 59.92 ms for enrolled startup, 42.44 ms for unenrolled startup and 97.67 ms for the read-only guard. Context stayed within 73 words. These are local subprocess measurements, not native p95. Direct native CLI hook observations were individual timings; no native latency distribution or token savings is claimed.

Only local APFS storage on macOS is observed. Windows, network filesystems, desktop UI execution, Claude lifecycle, model policy compliance, arbitrary shell/external edits, full integration isolation, readiness and merge enforcement remain unqualified or belong to later deliverables. Nine existing third-party hooks retained their exact guards, whose executable paths were inert under those observed conditions. No user hooks or settings were changed; this does not qualify every integration path.

See [Codex task runtime](../clients/runtime.md) for controls and supported limits. Package validation and fixture tests are separate from these native observations.
