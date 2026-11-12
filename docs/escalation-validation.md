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

## Capability ladder — 9 November 2026

The user replaced the single stronger diagnosis with a ladder: when repairs
stall, escalate to a more capable model, then again, at most five times per
target, and only then stop and ask the user with enough context to decide.
[Stuck-pattern escalation](../policies/03-agent-delegation.md#stuck-pattern-escalation)
now stops each level immediately, climbs to the lightest authorized option
stronger than every earlier level (a stronger model or higher reasoning
effort, including routes of activated integrations), resumes repairs only after
an accepted diagnosis, and asks the user only after the fifth escalation, when
no stronger option remains, or when an original limit would be exceeded. The
request carries the goal, every level's attempt, the remaining hypotheses,
options with cost and risk, a recommendation and the exact decision. Re-scoping
does not reset the count; budgets, ownership and coordinator settings still
bind.

### Instruction paths

| Representative task | Expected path and outcome |
| --- | --- |
| Coordinator repair churn on one target | Stop at the current level; escalate one level to an independent worker with the full history; diagnosis before edits. |
| Churn recurs after an accepted diagnosis | Climb one more level; no user involvement yet. |
| The fifth escalation does not converge | Stop and send the decidable request; hold affected work, continue independent work. |
| No stronger authorized option, or a limit would be exceeded | Stop early with the same request; never reset budgets. |
| Splitting or renaming the failing criteria | The count continues. |
| One new defect while earlier criteria hold | No stuck pattern; ordinary scoped fix. |
| The next level exists only on an activated integration | Use it without asking, under worker selection and the shared external contract. |

### Independent evaluation

A fresh native agent (independent of the author, read-only, with no access to
`docs/`, this record or the author's expected outcomes) applied the instructions
to seven ladder histories using an abstract ascending capability list. All seven
decisions matched the author's privately recorded expectations: stop and climb
one level at a time, no user involvement before the stopping conditions, a
decidable request at the fifth escalation, early stops for an exhausted ladder
or budget, no reset by splitting the criteria, no escalation for a single new
finding, and no extra approval for an activated-integration level. It found one
gap, what happens after the user decides; step 4 now says the decision sets the
next step and the history carries forward.

### Checks and limits

`python3 -B scripts/validate.py` passed with every skill entry within 150 words,
and the authoring link check resolved every local file and anchor reference in
the changed Markdown files. The instruction-reading modules passed in the final
run (among them the startup, minimal-default, prepared-instruction, setup-source, project-review and package modules). An independent behavior and security review of
the whole branch found no defect in the ladder text. This is instruction
validation: no live repair, dispatch, installed-plugin restart or model
comparison ran, and detection remains the agent's responsibility.

## Three escalations and re-evaluation — 12 November 2026

The user shortened the ladder and added a reflection step before involving
them ([design](superpowers/specs/2026-11-12-opencode-escalation-gate-design.md#policy-changes)).
[Stuck-pattern escalation](../policies/03-agent-delegation.md#stuck-pattern-escalation)
now allows at most three escalations per target, states that escalation within
authorized options needs no user approval, and from the second escalation asks
whether the design itself is the cause. When the third escalation does not
converge, or no stronger option remains, one architecture re-evaluation checks
whether a simpler solution meets every agreed requirement. An accepted
re-evaluation resets the escalation count once per target; budgets never reset.
[Project Scope](../policies/01-project-scope.md#when-instructions-conflict) now
makes the agreed requirements the hard gate and lets agents decide architecture
within them, with independent review of a material redesign.

### Instruction paths

Author walks. The policy index loads Agent Delegation on stalled repair or
validation progress; the re-evaluation step links to Project Scope for the
definition of agreed requirements, so no other contract is required.

| Representative task | Expected path and outcome |
| --- | --- |
| First escalation on a target | Stronger diagnosis without a design question; no user approval requested. |
| Second escalation on the same target | The brief also asks whether the design itself is the cause. |
| Third escalation fails; a simpler design keeps every agreed requirement | Re-evaluation by the most capable authorized option, independent review of the redesign, coordinator accepts; the escalation count resets once. |
| The simpler design would drop or weaken an agreed requirement | Question to the user; hold affected work, continue independent work. |
| No stronger option remains before the third escalation | Re-evaluation first, then the user only if it does not converge. |
| Three more escalations fail after the reset | Decidable request to the user, including the re-evaluation's outcome. |
| An original limit would be exceeded at any point | Stop and ask; no budget resets. |
| The agent is about to ask permission for a stronger diagnosis | No approval needed; escalate. |
| One new defect while earlier criteria hold | No stuck pattern; ordinary scoped fix. |

### Checks and limits

`python3 -B scripts/validate.py` and the instruction-reading modules selected for
this change cover the package. These are author walks: no independent instruction
evaluation, live repair, dispatch or installed-plugin restart ran for this change.
The optional OpenCode gate supports, but does not replace, these instructions.
