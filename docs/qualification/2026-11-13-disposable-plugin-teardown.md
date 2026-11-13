# Disposable form-gating teardown investigation

## Scope and plan

The user requested diagnosis and the smallest offline-verified controller repair.
This is plugin teardown, not Git cleanup or shipped gate integration. Original
candidate `864b10dd4a3fa403d7f830717d329471d6949bac` and PR #289 remain unchanged.
PR #289 must remain draft/BLOCKED. No live allocation is available.

Task destination: branch `fix/disposable-plugin-teardown`, created from local
`main` at `0ce70fbeea70f98999e69d5394a8414f17fed927` in a fresh native worktree.
Primary remains on `main`; its four pre-existing untracked client files are not
task work. Original qualification worktree is clean at the published candidate.
Policies: current repository scope/planning/handoff/development, integration,
test, security, review and publication contracts. Setup authority remains the
primary checkout's `docs/setup.md`. No feature setting change is needed.

Design: keep the original controller and evidence immutable. Version the offline
revision in `tests/qualification/form_gate_controller.py` in this task worktree.
Retain historical case definitions for comparison, but disable live execution.
Extract only unload observation and final-report handling into
`tests/qualification/form_gate_cleanup.py`, a development-only helper with no
command-line entry point. Keep form cancellation and the exact
configuration-ownership check in the controller.
Use fake time and transport for regression tests; never execute native cases.
Do not add a general cleanup framework, dependency, service or settings control.

- [x] Inspect original sources, receipts, digests and installed lifecycle code.
- [x] Reproduce early observation failure and lost reporting with offline tests.
- [x] Pace observations inside the same 30-second/10-operation limits, reserve
      final RPC/process checks, and retain unknown/error outcomes as failures.
- [x] Wire the revised controller; test deadlines, timeouts, budgets,
      configuration failures and simultaneous case/cleanup failures.
- [x] Validate, commit, run affected committed tests and review.
- [x] Publish draft
      BLOCKED findings. Independent review and live qualification stay explicit.

Acceptance: delayed and immediate unload can pass only with timely positive
observations; never-unload, API faults, exhausted time/count and missing evidence
cannot pass. Case and cleanup results survive independently. No mutation replay,
deadline extension, budget reset or manual recovery is part of success.

## Investigation

Original private evidence root: preparation directory
`question-box-gating-20261113`. References below use paths relative to that root,
unless explicitly named as repository files or installed binary byte ranges.
The new private preparation directory `question-box-cleanup-20261113` retains a
24-file original manifest, source-only disposal characterizations and this task's
private review/evidence. It is not a live run or renewed allocation.

### Controller defects

1. `run.py:75-108` starts a 30-second recovery window, but `94-98` makes exactly
   four consecutive inventory reads with no sleep or condition-based wait.
   The deadline only guards starting calls (`27-38`); it never controls the
   observation loop. Four present snapshots therefore reach the assertion even
   when substantial observation time and three operations remain. This is a
   controller observation race, not proof that native unloading failed.
2. `run.py:256-268` catches exceptions from the case body, not from its own
   `finally`. `teardown()` raises at `108` before `result.json` is saved at `265`.
   A simultaneous case failure would retain its `failure.json`, but the raised
   cleanup assertion would mask it as the exit exception. This run had no case
   exception, so neither `result.json` nor `failure.json` was written.
3. `run.py:31-38` checks only before starting a subprocess. Its 0.5-second timeout
   floor can exceed the remaining allowance, and subprocess timeouts bypass the
   receipt append. `76-77` would also establish a new recovery deadline if called
   again. These are separate latent controller defects, not observed causes of
   the original four-read failure.

### Native lifecycle: asynchronous, with separate guarantees

The preserved installed CLI has SHA-256
`2160e46e87e83561195271e69d0385440bbc91bc4b9c8dc38e0a3822190614a1`.
Coordinates are zero-based byte offset plus length in that binary, not source
line numbers. Read-only extraction inspected:

| Surface | Installed 2.2.7 source evidence | Meaning |
| --- | --- | --- |
| Configuration propagation | `155702500+2100`, `Config.reload`, watcher queue debounce `100 millis`; `156595000+5000`, `u1`, another source-load debounce `100 millis` | Writing the owned configuration is not a synchronous unload barrier. These stages do not give an end-to-end latency bound. |
| Inventory | `156377100+4300`, `Plugin.list` returns cached `H`; `Plugin.activate` deletes an activation, awaits its scope close, then assigns `H=U()` | Rapid reads can keep returning the old inventory during reconciliation. Local `closed` precedes completion of the enclosing native scope; it is not itself an inventory receipt. |
| Promise cleanup | `152569915+8585`, `hle`, setup finalizer awaits the function returned by setup | The plugin's asynchronous disposal participates in native scope shutdown. There is no deadline in the inspected Promise cleanup adapter. |
| RPC | `156343000+3800`, `Rpc.register` removes its registration; `Rpc.call` returns `rpc.unavailable` when closed or unregistered | RPC state is a separate registry observation. RPC unavailability alone does not prove inventory absence or a successful full disposal. |

The current official [server-plugin lifecycle](https://opencode.ai/docs/build/plugins/server)
and [RPC guide](https://opencode.ai/docs/build/plugins/rpc) describe cleanup and
registration disposal, not an atomic configuration/inventory/RPC barrier or a
30-second service guarantee. Context7 returned quota exhaustion; direct official
documentation plus the preserved version's installed source supplied evidence.

No native lifecycle defect is established by this run. Cached inventory during
asynchronous unload matches the inspected implementation. This does not prove
that every unload finishes or that every internal scope has fully closed when
the plugin emits its own `closed` marker.

### What the timestamps do and do not measure

- `api.jsonl:40-41`: two empty owned-form lists; last response at
  `1794599770.3382761` seconds.
- `api.jsonl:42-45`: four inventory responses, all still containing the plugin,
  from `1794599770.3841422` to `1794599770.544564` seconds.
- `api.jsonl:46`: server-info response at `1794599770.592854` seconds.
- `events.jsonl:107`: matching-generation `closed` at `1794599770656` milliseconds.

Thus `closed` is approximately **63.145996 ms after server-info receipt**, or
111.436 ms after the last original inventory receipt. Neither is inventory
removal latency. Source order bounds the owned configuration write between the
second form-list receipt and the first inventory receipt. Subject to wall-clock
resolution/continuity, write-to-local-`closed` is roughly **272–318 ms**. There is
no recorded exact configuration-write timestamp, native watcher/close-start
timestamp or contemporaneous first-absent inventory receipt. Do not turn this
bounded local marker interval into an exact native unload latency.

`teardown-completion.json:14-37` contains three delayed metadata-only observations.
Its final observation is about 68.929 seconds after the first recovery receipt
(`independent-review.md:55-62`). This is delayed readback, **not 69 seconds spent
unloading**. Inspection/conversation consumed the recovery observation window.
The seven original plus three later operations exhausted the count. Those later
facts do not repair the original controller exit or its failed timing criterion.
No mutation or case was replayed; no restart/global reload occurred.

### Separate disposal risk

`plugin/index.ts:99-106` sets `stopped`, aborts the event stream, awaits `stream`,
then disposes registrations sequentially. A non-settling stream can prevent all
explicit registration disposal. A rejecting disposal can skip later registrations
and the `closed` log. `165` skips cleanup after `stopped` becomes true, rather than
joining an in-progress/failed close. The custom close RPC at `162` is not a
durable unload operation and is not used by this fix.

Two offline characterizations executed the exact extracted close body with a
pending stream and a rejecting registration. Both confirmed these control-flow
risks without loading a plugin, making API calls or running a canary. They do
not show that the installed native event stream actually ignores abort, or that
native registration finalizers fail. The retained successful `closed` marker
shows neither risk caused this historical failure. Plugin code remains unchanged;
the revised controller records missing matching-generation disposal evidence as
failed/unknown cleanup instead of waiting forever or claiming success.

## Smallest repair and verification

- The revised `teardown()` keeps cancellation, exact owned-registration removal
  and exclusive preimage archiving. It reserves pending session-list work plus
  three metadata operations before cancelling additional forms. Insufficient
  cancellation budget stays a failure, even if unloading later succeeds.
- `observe_unload()` reserves two operations for exact HTTP 400 / `RpcError` /
  `rpc.unavailable` verification and process identity. With two empty form lists,
  at most six inventory reads remain. Reads are paced across the available window,
  with six seconds reserved for the last inventory/RPC/process calls (at most two
  seconds each). Actual timeouts shrink when less time remains. Immediate absence
  needs only three metadata reads. Errors are not retried.
- A monotonic recovery deadline is established once, limited by both 30 seconds
  and the original overall remaining allocation. Counters are not reset. Calls
  have no minimum-timeout floor, log timeout/spawn errors and reject late replies.
  Re-entering teardown cannot replay configuration removal or renew the deadline.
- `teardown.json` is saved in its own `finally`, including configuration/observation
  failures. `finalize()` catches cleanup exceptions, writes `result.json` with
  separate case and recovery outcomes, and requires both to pass. Reporting still
  requires a writable destination; it cannot guarantee durable output after an
  OS/storage failure. The revision's command-line entry refuses live execution.
- Existing `Run.wait_idle` (`tests/qualification/opencode_live.py:192-203`),
  `wait_integration` (`chatgpt.py:79-95`) and `await_connection`
  (`gateway.py:169-186`) provide polling patterns, not this fixture's shared
  count/deadline/final-verification reservation. Reusing their service launchers,
  authentication flows or unbounded-by-count loops would expand scope. Only two
  local functions were extracted; no generic cleanup framework was added.

RED: committed baseline extraction `c2672de` ran 16 tests: 6 assertion failures,
7 uncaught original failure-path errors, 3 passes. The delayed-unload reproduction
failed specifically because four immediate reads still reported the subject.
Separately, source-only controller checks reproduced the original teardown
assertion and missing failure receipts. A cancellation-reservation regression
also failed before its focused fix.

GREEN at `cd789c1`: `python3 -B scripts/test_sharded.py test_form_gate_cleanup
test_qualification_harness test_opencode_workflow` passed **83 tests**. An initial
command named nonexistent `test_opencode_workflow_gate`; the selector rejected it
before running tests. This is not counted as test execution. Additional committed
controller checks and final validation/review are recorded below.

At `082c50b8d46dea3b4cee6e436e9cb54c23192d23`,
`python3 -B scripts/test_sharded.py test_form_gate_cleanup test_form_gate_controller
test_qualification_harness test_opencode_workflow` passed **91 tests**, with no
skips. This includes 21 helper tests and 8 actual-controller tests. The controller
tests substitute only time, subprocess transport and disposable file paths; the
real receipt, budget, configuration mutation and teardown code execute. Module
entry refusal was also checked in a local Python subprocess: exit 1 with the
expected offline-only message, not a native controller run.

Independent native child `ses_e293f2d9effeC1xkcCCarixSA6` reviewed all five files
and connected original evidence at that exact head against the recorded base.
The reviewer independently ran 29 helper/controller tests, endpoint diff checks
and the 24-file original-manifest comparison. All passed; no findings were
substantiated. The review accepted the offline repair, not native qualification.
Configured/executed child model and effort were not observed; native dispatch and
completion establish independence. Visibility preparation was unavailable. The
coordinator's title helper returned `invalid_request`; no title delivery is claimed.
This native review is a separate model assignment, not zero total model usage or
part of the exhausted case/recovery allocation. A private copy of the returned
review preserves its limits. Final documentation only records these results;
reviewed controller/helper/test bytes remain unchanged.

Package validation passed with the existing 75%-bundle-size warning. Python
3.15.2 and Node v26.13.0 were observed. No linter/type/build command exists here.
The full repository suite is not selected: changed code is development-only and
isolated from shipped runtime; focused controller/helper and nearest qualification
and gate tests cover affected behavior. No original successful case, controller,
native lifecycle qualification or shipped client execution is rerun.

## Remaining boundary

These are offline proofs, not a new native cleanup pass. A finite 10-operation
allocation cannot guarantee arbitrary native latency or an unload after its last
scheduled inventory read. The reserved terminal window is conservative and may
return a truthful failure while a later native unload completes. General watcher,
abort responsiveness, registration finalizer behavior, reload/replacement races
and durable absence remain unqualified. Any new live trial needs its own approved
scope and budget. No unspent old allowance exists. PR #289 remains draft/BLOCKED.

Published offline repair: [draft PR #291](https://github.com/fcoouto/vibe-workflow/pull/291),
branch `fix/disposable-plugin-teardown`. Final committed check at
`07fc5ea99b3ab309ea070905ceedeee87470a2a5` passed the same 91 tests without skips.
The independent review's four code/test files are unchanged; later edits record
verification/publication only. PR #291 also remains draft/BLOCKED rather than
implying a native cleanup qualification. Next possible native work belongs to a
separately approved qualification scope, not automatic recovery or case replay.
