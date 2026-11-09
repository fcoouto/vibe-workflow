# Resource-aware heavy execution

Load when resource-managed execution is selected for a heavy build, test,
browser/database workload or other resource-intensive local phase. Check
`resource_admission` before a new helper-managed launch. This
feature is independent of capacity recovery and ordinary delegation. Admission is
cooperative, not OS containment; task budgets and native/account limits still apply.

Use the installed `scripts/workflow.py delegation workstreams ACTION --cwd CHECKOUT`
with one JSON object on stdin. Actions are `resource-run`, `resource-status`,
`resource-cancel` and `resource-release`. A new `resource-run` carries current task
identity (`client`, `session_id`, `task_id`, `scope_ref`, `expected_revision`),
original `attempt_id`, current `resource_revision` and a stable `phase_id`. Use an
admitted coordinator, worker or reviewer assignment. The helper checks current
pins, task, resource preference, dependencies and ownership. It never repins old
tasks. Status, cancel and release remain available after disable and retain their
original assignment/phase authority; they do not require an enabled resource feature.

For `resource-run`, add `argv` (an argument array, no shell wrapping required),
`cpu`, `memory_bytes`, `capacity_cpu` and `capacity_memory_bytes`. Demand and the
shared allowance are selected for the task; use known command parallelism and
leave user headroom. Each participating parent must use this same host helper.
Smaller active declared allowances constrain the shared pool. Choose allowances
honestly; these declarations are cooperative, not independent hardware enforcement.
Optional fields: `wait_seconds` (0–60, default 0), `timeout_seconds` (positive,
clamped to original task deadline), `disk_bytes` (required free disk headroom,
default 0). Set `background:true` for an intentional persistent service; it retains
capacity until reconciliation. Existing service/port write claims also retain
capacity. Omit `background` for a finite foreground command. For example, `argv:["python3","-B","-m","unittest"]`
runs the selected checkout's test command within the reserved heavy phase.

The private host registry is `$CODEX_HOME/workstreams-resources` (default
`~/.codex/workstreams-resources`), shared across checkouts on that machine. Do not
edit it manually. Atomic reservations account for approved demand before commands
start. Memory observations are cached briefly across checkouts. Disk headroom is
checked independently for each admission attempt that declares disk demand. Measurement errors permit admission through all remaining
gates and return a warning; they never erase occupied reservations or ownership.
High CPU utilization alone does not veto work. Memory estimates conservatively
count active declared memory as well as observed consumption, which can reduce
concurrency. No automatic profiler or model-driven monitor is added.

A disabled resource preference blocks new helper-managed runs, not ordinary
delegation. Disabling does not cancel a running command or erase a reservation.
Existing safe cleanup and status remain available. A waiting result includes
`reason` and `next_recheck`. Older ready competing
requests precede newer requests; blocked requests do not reserve heavy capacity.
Use completion/dependency events and bounded helper rechecks while a participant
is active. `wait_seconds` performs deterministic rechecks without extra model turns,
rechecking the resource preference, task and dependencies each time. At the bound, return to coordination;
never loop beyond task deadline or create another schedule. Human continuation
resumes after all participants shut down.

A phase can launch only on its first admitted transition. Reusing its identity
never reruns a command, even after completion. A lost acknowledgment requires
inspection of that identity and native execution, not replay. The helper returns
exit code and at most 16 KiB each of stdout/stderr. Timeouts leave the original
command running and retain its reservation; returned PID is evidence for inspection,
not permission to stop unrelated processes.

A completed finite foreground command releases cooperative host capacity after
the helper observes its exit, including a nonzero exit. Its actual exit code still
determines whether the command passed. This releases no assignment, writer claim,
review or recovery authority and does not certify that unknown descendants stopped.
The returned `released` and `uncertain` fields describe capacity only. The same
phase cannot replay after completion; a new phase may use the freed capacity.

Known background services and timed-out commands retain capacity. For retained
capacity, `resource-release` verifies independent reconciliation of the owner or
the exact phase. For a completed phase while its assignment continues, supply
`host_proof:{path,sha256}` referring to private independently observed JSON with
exactly `version:1`, `kind:"resource-phase-reconciliation"`, `reservation_id`,
`owner` copied from resource status, original `phase_id`, `execution_digest` from the returned completed execution receipt, `stopped:true`,
`descendants_stopped:true`, `observation_ref` and `coordinator_ref`. The observation
must establish that this phase and all its descendants stopped; flags alone are
not evidence. A proof for another phase, execution or owner rejects. An admitted phase without a durable completed execution receipt cannot use phase-only release, so a pending launcher cannot run after its capacity was freed. After release, a new
phase can use the freed capacity; simultaneous or uncertain phases retain their
separate occupancy. Without a phase proof, the original whole-owner D6
reconciliation remains supported.
`resource-cancel` cancels only an unlaunched wait atomically. Cleanup remains
available after disable/cancellation. Time expiry alone never frees an active
reservation. Known disabled or closed older waiters stop blocking the queue;
unreadable authority retains its guard for inspection. Status preserves the last
waiting reason/recheck and identifies active reservations needing stop proof.

Completed audit remains until the original deadline; expired terminal records are
archived privately before removal. The live/unexpired JSON registry remains bounded
at 64 KiB; D6 inputs/artifacts and shared-ledger technical bounds remain. Exhausting
those integrity bounds is an explicit error, not a telemetry failure or a promise
of unlimited concurrency. See [resource qualification](../docs/workstreams-main-port.md#validation-boundary) for measured scope.
