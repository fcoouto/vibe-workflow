# Stuck-pattern escalation validation

1 November 2026. The user requested immediate, model-agnostic escalation when
repairs stall, including cycles that keep producing new findings. The previous
contract allowed evidence-backed retries and discretionary escalation without
requiring a stop when those retries failed to converge.

## Instruction paths

Author-performed scenario walks checked the policy index, Agent Delegation,
Tests and Builds, and Code Review. These are instruction checks, not observed
client execution or an automatic detector test.

| Representative task | Instruction path and outcome |
| --- | --- |
| Coordinator repeatedly repairs one feature; each validation uncovers a different blocking finding without verified progress | Index loads Delegation on stalled progress; Tests and Builds routes to escalation. Stop before another fix and obtain stronger independent diagnosis despite fresh evidence. |
| A worker's repairs reintroduce an earlier defect under deadline pressure | Delegation applies directly. Stop and report; hand off attempts, findings, checks and remaining allowance. Existing stopped-writer rules protect ownership. |
| Review keeps returning new findings after each fix | Code Review routes to the same escalation rule before returning more repairs. New evidence does not bypass the stop; internal fixes cannot reset review rounds. |
| One new defect is found while earlier criteria are verifiably satisfied | No stuck pattern is established. Continue the ordinary scoped fix and relevant checks within existing limits. |
| Stronger diagnosis is unavailable or the original budget is exhausted | Report the blocker; hold affected work and continue independent work. Do not switch the coordinator, expand permissions or reset allowances. |
| Diagnosis recommends another attempt | Coordinator requires evidence, a changed approach and a focused verification plan before resuming. Unresolved findings and quality gates remain binding. |

The policy defines escalation once. Runtime loading follows local contract links;
this source record, Toolkit research, Council and external-worker procedures stay
unloaded unless their separate triggers apply. Native dispatch loads existing
client guidance only when needed. Skill entry points and runtime schemas are
unchanged.

## Verification boundary

### Independent agent evaluation

A fresh native agent independently applied the policies to eight simulated task
histories, with two capacity/budget branches in one history: nine decisions passed.
The agent could not read this validation record or the author's expected outcomes
and remained read-only. No confirmed instruction defect required a policy change.

Cases covered fresh-finding churn, verified incremental progress, alternating
authorization regressions, review-round resets, unavailable stronger diagnosis,
exhausted allowances, a single new review finding, rising test counts masking
regressions, and renamed continuation work with an unsupported recovery report.
Fresh findings did not bypass escalation; settings, ownership and original limits
remained binding. Genuine verified progress did not trigger escalation by itself.

Detection timing depends on observed acceptance progress: missing per-round
snapshots cannot establish an earlier exact detection point or justify inventing
a numeric retry threshold. This was one independent instruction evaluation,
not a live repair, dispatch or mechanical enforcement test.

### Controlled live exercise

A disposable, credential-free workspace reproduced three controlled candidates:
insufficient stock was accepted, a repair left partial batch writes, and the next
repair deducted inventory again on request replay. A fresh native worker ran the
current checks and requested stronger independent diagnosis before editing,
despite three remaining repair attempts. All fixture and budget hashes remained
unchanged until the coordinator accepted the diagnosis.

The diagnostic worker independently reproduced the failure and checked rollback,
retry and repeated-item behavior. One coordinator recovery edit then passed all
three acceptance checks and four focused regressions. The exercise used 243 of
720 seconds, three of five repair attempts, two of two worker dispatches, one
diagnostic pass and one review round. Coordinator settings and original budgets
were preserved; per-turn child model/effort execution was not independently exposed.

Earlier repair candidates were controlled injections, not autonomous worker
repairs. This demonstrates one live escalation and recovery, not general
detection reliability, sandbox enforcement or a restarted installed-plugin run.
Private fixture, hashes, native task identities and outcomes were retained as
local evaluation evidence outside the repository.

### Package checks and execution limits

`python3 -B scripts/validate.py` passed; all skill entries remain within 150 words.
`git diff --check` passed. Original authoring checks resolved 136 local Markdown
file and anchor references. The isolated main candidate passed package validation
and all 79 local references across its six affected files; the count differs
because main contains a different surrounding source map.
No installed-plugin restart or model comparison was performed.
Detection remains the agent's responsibility; existing runtime admission limits
do not detect semantic repair churn.
