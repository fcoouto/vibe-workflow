# Resource-aware heavy execution

Load for a heavy build, test, browser/database workload or other resource-intensive
local phase. Check `resource_admission` before a new helper-managed launch. This
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
default 0). Persistent services retain their owner reservation. For example, `argv:["python3","-B","-m","unittest"]`
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

Command exit alone does not release capacity: an arbitrary command can detach a
service into another process group. Reservations remain active until
`resource-release` verifies existing independent D6 reconciliation that the entire
original owner stopped. This conservative v1 rule can hold capacity until a worker
turn is reconciled; it does not guarantee prompt release after each shell command.
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
