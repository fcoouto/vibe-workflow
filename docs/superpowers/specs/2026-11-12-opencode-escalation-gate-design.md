# OpenCode stuck-pattern escalation gate

## Intent

Agents know the [stuck-pattern escalation](../../../policies/03-agent-delegation.md#stuck-pattern-escalation)
rule and still skip it. In the motivating session, a coordinator kept dispatching
same-level fixes and reviews while one invariant ("a restart must not replenish the
approved allowance") stayed broken through new paths. Asked afterwards, it could
recite the rule, said it had evaluated findings one by one, preferred the generic
fix loop, and treated recording the policy as applying it. In a later session the
same pattern appeared differently: the agent stopped to ask permission for a
stronger diagnosis it was already authorized to run, and the useful step came only
when the user asked whether the architecture itself should be simplified.

The policy text already says that renaming findings does not reset the count and
that a same-level reviewer is not escalation. More wording will not fix it. The fix
is a check at the moment of the next repair, using facts the host can observe, plus
two policy improvements the user requested: a shorter escalation ladder and an
architecture re-evaluation before the user is involved.

The user wants a vibe-workflow OpenCode plugin only if it adds real end-user value
through stronger enforcement of workflow steps, kept thin: no runtime controls
beyond what the failure needs. Smart Routing, which will route every subagent task,
routes the gate's judge from the start; the rest of the gate does not depend on it.

## Goals

- Hold further same-level repairs of files that have already been repaired twice
  and failed again, until a stronger read-only diagnosis has run.
- Count escalation only from the route the host actually sent, never from a report
  or a brief.
- Let a small routed judge decide whether a new failure is the same problem and
  whether the next repair is a materially different approach.
- Shorten the policy's ladder to three escalations, add an architecture
  re-evaluation after it, and let agents decide architecture autonomously inside
  the agreed requirements.
- Stay silent and cheap when nothing is stuck.

## Non-goals

- A declared repair ledger, assessment tool, invariant identifiers, reset command,
  reset tool or `question`-tool reset offer.
- Reminders injected into every request.
- The primary-checkout guard, merge-readiness gate or review-independence
  verification. They are later candidates.
- Building Smart Routing's interactive path. The gate's judge waits for it.
- Codex or Claude Code adapters, and onboarding OpenCode as an enrollment client.
- Enforcement against writes made through the shell, `` !`cmd` `` command
  templates, other plugins or the server API.

## Anti-goals

The work fails, even if it runs, when it:

- trips on an ordinary fix loop (fail, fix, pass) or on first edits after
  exploration;
- prompts the user before the policy's own stop conditions;
- prints anything or calls a model when nothing is stuck;
- names commercial models in instructions or policies;
- blocks ordinary editing because the plugin itself failed;
- presents itself as a security boundary.

## Constraints

- OpenCode V2 server plugin API, as documented at
  <https://opencode.ai/docs/build/plugins/server>; installed host OpenCode 2.2.6.
- Hooks used: `tool.execute.before`, `tool.execute.after`, `permission.evaluate`,
  the session `prompt` admission hook, `session.created` events and the
  `model.request` hook that the routing shim already uses. Nothing marked
  experimental.
- Dependency-free TypeScript like
  [`opencode_plugin.ts`](../../../runtime/adapters/opencode_plugin.ts), tested with
  `node --test`; no package manager.
- Enabled per project through [feature settings](../../../clients/features.md),
  default off.
- Portable rules name no commercial models
  ([Agent Delegation](../../../policies/03-agent-delegation.md#select-capability-and-dispatch)).
  Instructions say, for example, "the least capable model sufficient for the
  task"; concrete models appear only in user settings and routing data.
- The user's lightweight rule for Smart Routing: reuse existing data, no polling,
  minimal processes, and every added cost named. The judge is such a cost: one
  routed call at a trip and at most one per new evidence during a hold.
- [Project Scope](../../../policies/01-project-scope.md): no qualification claim
  without observed client execution.
- The routing shim and managed launcher are unchanged.

## Approaches

1. **Reminders only.** Inject a repair-round count and a policy pointer into
   requests. Cheapest, but it targets the failure that already happened (the agent
   knew the rule), and the [hook pilot](../../qualification/2026-11-11-template-hook-pilot/report.md)
   measured advisory hooks adding tokens without changing behavior. Rejected.
2. **Fact-based trip, hold and verified release ("thin B").** Detect recurrence
   from re-edits and failures, hold further repairs of those files, release only
   after the host has observed a stronger read-only diagnosis. No model calls.
   Its weakness is coarse semantics: an unrelated failure on the same files costs
   an unnecessary diagnosis, and "a stronger model ran" is weaker than the policy's
   "evidence-backed diagnosis and a changed approach".
3. **Approach 2 plus a judge at every repair dispatch.** Better semantics, but a
   model call per dispatch. Rejected as too heavy.
4. **Approach 2 plus a routed judge only after a trip (selected).** The mechanical
   rule decides when something may be stuck. A small routed judge then decides
   "same problem?" and, at release, "evidence-backed root cause and materially
   different approach?". The stronger-diagnosis fact stays a precondition, so a
   persuasive same-level fix cannot pass on the judge's word alone.

The user chose approach 4 on 12 November 2026, with these decisions:

- The judge is a Smart Routing task from the beginning, not a fallback. If routing
  cannot route it yet, the judge waits for that implementation.
- The mechanical gate ships first; the judge is added when routing can route it.
- The `escalation_gate` feature keeps consent `none`; the judge's cost is
  authorized and bounded by Smart Routing, not by the gate.
- The judge is the main reset path; the user's plain message is the override. No
  command, tool or `question`-tool offer.
- Escalation stops at three, followed by an architecture re-evaluation that resets
  the count once per target.
- Agents decide architecture within the agreed requirements; the agreed
  requirements are the hard gate.

An earlier proposal to start with a primary-checkout guard was set aside after the
user's example showed escalation as the higher-value gap.

## Policy changes

### Project Scope: requirements as the hard gate

Replace the first sentence of the "Ask only when" paragraph in
[Project Scope](../../../policies/01-project-scope.md#when-instructions-conflict):

Before:

> Ask only when an unresolved choice materially changes scope, behavior, cost,
> access, architecture or enforcement. Use stated assumptions for minor reversible
> details. Reopen only affected decisions after material changes; never treat a
> recommendation or missing answer as approval. Record decision scope, provenance,
> affected actions and what would invalidate it. Pending decisions block only
> dependent work.

After (approved by the user):

> Agreed requirements are the hard gate: the agreed plan and acceptance criteria,
> the user's explicit constraints and recorded decisions. Ask before any choice
> that would change, drop or weaken one of them, or that changes scope, cost,
> access, enforcement or user-visible behavior they do not cover. Within them,
> decide architecture and implementation yourself, including a simpler redesign; a
> material redesign needs independent review before you accept it, and its record
> states how each agreed requirement remains met. Use stated assumptions for minor
> reversible details. Reopen only affected decisions after material changes; never
> treat a recommendation or missing answer as approval. Record decision scope,
> provenance, affected actions and what would invalidate it. Pending decisions
> block only dependent work.

### Agent Delegation: stuck-pattern escalation

Change [stuck-pattern escalation](../../../policies/03-agent-delegation.md#stuck-pattern-escalation)
as follows, keeping every other requirement:

- **No approval needed.** Add at the point of use: escalation within authorized
  options needs no user approval.
- **Design question from the second escalation.** From the second escalation on a
  target, the diagnosis brief also asks whether the design itself is the cause.
- **Three escalations.** "Escalate at most five times per target" becomes "at most
  three times".
- **Architecture re-evaluation.** When the third escalation does not converge, or
  no more capable authorized option remains, run one architecture re-evaluation
  before involving the user:
  - Input: the agreed requirements as defined in Project Scope. The coordinator
    represents the user and passes all of them to the architect.
  - Performed by the most capable authorized option. Add a second independent
    assessment from another model family only when one is available and the first
    does not converge.
  - Question: what are we doing, and could a simpler solution meet every agreed
    requirement?
  - A proposed redesign gets an independent review. The coordinator accepts it when
    every agreed requirement remains met, as Project Scope allows.
  - A redesign that would change, drop or weaken an agreed requirement becomes a
    question to the user.
  - An accepted re-evaluation resets the escalation count once per target.
- **Stop conditions.** After the reset, stop and ask the user when three more
  escalations do not converge, no more capable authorized option remains, or an
  original limit would be exceeded. Time, attempt, resource and review-round
  budgets never reset.
- **Pointer.** One line pointing to the optional OpenCode gate as a mechanism, not
  a second copy of the rule.

## Gate design

### Scope of observation

The plugin watches one **tree**: a root session the user works in, plus every
descendant session learned from `session.created` parent links (the same lineage
technique as the shim). Trees are independent. Sessions the routing shim treats as
managed are out of scope, and so are the gate's own judge sessions.

### Signals

- **Edit.** A `permission.evaluate` request with action `edit` (and `write` or
  `patch` if a later version uses them) from any session in the tree. Its
  resources, resolved against `ctx.location.directory` as the shim does, are the
  edited files. Only edits that the host lets run count.
- **Failure.** Either:
  - a **check failure**: a `shell` tool call whose command matches a check pattern
    and whose result reports a non-zero exit status in `tool.execute.after`.
    Patterns come from the feature's `check_patterns` setting, with defaults for
    common test and validation runners (for this repository,
    `scripts/validate.py` and `scripts/test_sharded.py`); or
  - a **review return**: a subagent session in the tree that made no edits
    finishes its run.
- **Route.** The route each session actually sends, read from `model.request`
  (latest wins): provider, model alias and variant, as the
  [adapter](../../../clients/opencode.md#route-identity) normalizes them. The
  provider's resolved model stays unknown; the sent route is still host-observed
  dispatch evidence. Requested models in a spawn are not used for ranking.
- **User message.** A prompt the user submits to the root session, seen by the
  `prompt` admission hook. Messages to child sessions, synthetic messages and
  shell messages are not user messages.

### Repair rounds and the trip

A **repair round** on file `f` is a re-edit of `f`, in any session of the tree,
after a failure that happened since `f`'s previous edit. First edits of a file never
count, so implementation after exploration is not a repair. Several edits to `f`
before the next failure are one round. Each round records the route of the session
that made it as a **level** on `f`.

File `f` **trips** when it has at least `repair_rounds` rounds (default `2`) and a
new failure has happened since its latest round. Passing checks never clear rounds:
in the motivating session, tests passed while the invariant stayed broken.

### Judge

The judge answers narrow questions for the gate. It is a Smart Routing task from
the start:

- Routing chooses its model, the least capable model sufficient for the task, and
  accounts for its usage. Its task profile is set explicitly (low complexity, a
  bounded token budget, no tools), so routing does not apply its high floors.
- The plugin prepares and dispatches it once through routing and runs it in a
  visible child session of the root, titled `escalation judge: <files>`, so the
  user can open its transcript. Starting a separate server per call, as the
  managed launcher does, is too heavy.
- Its input is bounded and fact-weighted: the files, the diff of each repair round,
  the failing check commands with the tail of their output, excerpts of reviewers'
  final messages, the route behind each round and, for release, the diagnosis
  excerpt and the coordinator's latest stated plan. Diffs and check output
  outweigh agent prose.
- Verdicts are cached by an evidence fingerprint. The judge runs again only when a
  new check result, diagnosis or diff arrives during a hold.
- While routing cannot route the judge, the judge does not run and the gate behaves
  mechanically. There is no other route and no fallback model.

**Call 1, at a trip: "same problem?"** `unrelated` clears the file's rounds and
nothing is held. `same` or `unsure` holds the file.

**Call 2, at release: "evidence-backed root cause and materially different
approach?"** Asked when the coordinator's next edit of a held file arrives after a
qualifying diagnosis. `yes` releases; `no` keeps the edit refused and returns the
judge's reason to the model.

### Hold

While `f` is held:

- An edit of `f` by any session in the tree is set to `deny` in
  `permission.evaluate`. The message names the held files, their round count, the
  escalation stage, the required next step and, when the judge ran, its verdict,
  one-line reason and judge session ID.
- A `subagent` follow-up (input with `sessionID`) to a session that made a round on
  a held file is refused in `tool.execute.before` with the same message. This is
  the "resume the same owner" path from the example.
- Everything else passes: new dispatches, reads, checks and edits of other files.

Explicitly configured `deny` rules are final in OpenCode and never reach the hook;
the gate only tightens.

### Release and escalation stages

A **diagnosis** is a new subagent session in the tree (not a follow-up), created
after the file was held, that made no edits and finished its run.

- **Escalations 1 to 3.** A held file is eligible for release when a diagnosis
  whose route ranks above every level on it completes. With the judge, release
  also needs call 2 to answer `yes`; without it, eligibility releases. Release
  clears the file's rounds, records the diagnosis route as a level and adds one
  escalation.
- **Re-evaluation.** After three escalations, or when no ranked option is above
  every level, the next hold asks for the architecture re-evaluation. A new
  read-only session whose route ranks at least as high as every level qualifies;
  with the judge, call 2 also checks that its report addresses the agreed
  requirements and a simpler solution. Its acceptance resets the escalation count
  once per file.
- **Stop.** After the reset is used, three more escalations without convergence,
  or no ranked option above every level, turn the file's holds into `ask` with a
  short message pointing to the policy's decision request. These are the only user
  prompts the gate creates.

Accepting the diagnosis or redesign stays the coordinator's job, as the policy
says. Without the judge, the gate checks that the required diagnosis happened, not
that it was good.

### Capability order

`rank(route)` compares routes:

- the feature's `ladder` setting: an ordered list of `provider/model` entries,
  weakest first, chosen by the user;
- within one model, variants in the order `low`, `medium`, `high`, `xhigh`, `max`.

A model missing from the ladder can only outrank the same model at a lower variant.
Unknown or unranked comparisons never count as stronger. "No ranked option above"
is computed from the ranking alone: the highest level is at `max` and no ladder
entry ranks above its model. In managed projects routing later replaces `rank`, so
escalation means "raise the task profile floor", as policy 03 already states.

### Reset

- **Judge (main path).** Call 1's `unrelated` clears a file's rounds automatically.
- **User override.** A user message in the root session that asks to reset the
  gate (mentioning a reset of the gate, counter, hold or escalation) clears the
  named files, or every held file when none is named. Detection is a simple
  phrase match, with negations such as "don't reset" excluded; once the judge
  exists, it reads the intent instead. The plugin confirms through a session
  instruction entry ("gate reset: `<files>`, by user") that the agent reports
  back. An agent cannot clear its own hold; the remaining path, a shell posting a
  prompt through the server API, is a known residual.

### Troubleshooting

- Hold messages carry the stage, verdict, reason and judge session ID.
- Judge sessions are visible children of the root.
- Each judge call is a routing attempt, visible in routing's `status`.
- A read-only status tool lets the agent answer "what is the gate doing?": held
  files, rounds, levels, escalation stage and recent verdicts.
- The plugin never logs prompts, diffs, check output or messages.

### State and failure behavior

- State per tree: per file, rounds, levels, escalation stage and order of last edit
  and failure; per session, parent, observed route and whether it edited; cached
  verdicts. Kept in memory and mirrored to `ctx.storage` so a plugin reload keeps
  holds. Bounded, with oldest trees dropped first.
- If the plugin throws, cannot resolve a path or loses state, it allows the action.
  Ordinary work never depends on it. The status tool reports the last internal
  error.
- No output, notices or model calls while nothing is held.

### Activation

- Feature `escalation_gate`: manifest `features/escalation_gate.json`, default
  `{"enabled": false}`, control `settings`, consent `none`, settings
  `repair_rounds`, `check_patterns`, `ladder`. The consent stays `none` when the
  judge is added: the gate itself sends nothing and spends nothing. A feature with
  any other consent can never be enabled through `features enable` and is never
  effective in the current `runtime/features.py`, which would switch off the whole
  gate, not only the judge.
- The judge's cost belongs to Smart Routing. The judge is a routed task, so it runs
  only where the user has activated Smart Routing for the project; that activation
  authorizes its routes within their saved scope and limits
  ([Agent Delegation](../../../policies/03-agent-delegation.md#select-capability-and-dispatch)),
  and routing's caps and usage windows bound what the judge uses. Without Smart
  Routing the judge does not run and the gate stays mechanical.
- The plugin reads the resolved value at setup through
  `python3 -I -B scripts/workflow.py features status escalation_gate --cwd <dir>`
  and again on `config.updated`. Disabled, missing or unreadable means the plugin
  does nothing.
- It loads from a path entry in the user's `plugins` configuration pointing at the
  installed package.

### Packaging

A new server plugin file, `runtime/adapters/opencode_workflow.ts`, separate from the
routing shim. The shim is loaded only by the managed launcher and does nothing for
interactive sessions; the gate is for interactive sessions. Lineage and
path-resolution helpers may be shared if that changes nothing in the shim.

### Smart Routing hook point

The gate owns one `subagent` `tool.execute.before` handler, one `rank` function and
one judge dispatch. Routing later replaces `rank`, chooses and admits the
diagnosis and re-evaluation models through the same handler, and already routes
the judge. None of this changes the trip, hold or release rules.

## Delivery order

1. Policy changes (Project Scope, Agent Delegation) and the mechanical gate:
   signals, trip, hold, stages, ranking, user override, status tool.
2. The judge, once Smart Routing can prepare, dispatch and settle an interactive
   routed task. Until then step 1 stands alone.

## Validation

1. **Unit tests** (`node --test`, fake plugin context, in the style of
   `tests/opencode_plugin/`): first edits never count; one fail-fix-pass loop never
   trips; trip after `repair_rounds`; passing checks never clear rounds; edit hold
   in coordinator and child; follow-up refusal; release only by a new, edit-free,
   higher-ranked session created after the hold; same-level reviewer never
   releases; three escalations lead to re-evaluation; one reset per file; `ask`
   only at the stop conditions; user override from root prompts only, with
   negation excluded; agent messages never reset; reload restores holds; plugin
   errors allow; disabled feature does nothing. With the judge: `unrelated` clears,
   `unsure` holds, cached verdicts reused, no judge call when nothing is held.
2. **Live probes on OpenCode 2.2.6**, each a qualification prerequisite before the
   gate is described as working:
   - `tool.execute.after` exposes a shell call's exit status;
   - `permission.evaluate` for `edit` carries the child's `sessionID`;
   - a `deny` message reaches the model and the edit does not happen;
   - a subagent follow-up carries `sessionID` in its input;
   - `model.request` reports each child's route;
   - the `prompt` hook sees root-session user prompts and not child prompts;
   - `ctx.storage` survives a plugin reload.
3. **Behavior evaluation**, with a separately approved subscription-only budget:
   - recurrence: a repair closes one reset path of an invariant and leaves another;
     the agent must run a stronger diagnosis before a third repair;
   - persistence: after three escalations the agent runs the re-evaluation and
     decides within the agreed requirements, asking only when one would change;
   - control: a genuinely unrelated failure, in other files and in the same files,
     to measure false trips and their cost;
   - with the judge: labelled same, unrelated and repeated-plan cases.

Report package checks, unit tests, live probes and behavior results separately.

## Documentation changes for the builder

- `policies/01-project-scope.md` and `policies/03-agent-delegation.md` as above;
  `docs/policy-sources.md` for the changed coverage; the regenerated instruction
  map.
- `clients/index.md`: a row for the gate, with its load trigger.
- A client contract for the gate (in `clients/opencode.md` or a new file), stating
  what it observes, what it enforces, its limits and its qualification status.
- `features/escalation_gate.json`.

## Left to the builder

- Default check patterns beyond this repository's commands.
- Bounds for stored trees, files, sessions and cached verdicts.
- Exact wording of hold messages and the reset phrase match, within the content
  listed above.
- The judge's prompt and input size limits.
- Whether to share lineage helpers with the shim or copy them.
