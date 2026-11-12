# OpenCode Escalation Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the policy changes and the mechanical OpenCode stuck-pattern escalation gate (delivery step 1 of the design); the routed judge gets its own plan once Smart Routing can route it.

**Architecture:** A pure, dependency-free TypeScript state machine (`gate.ts`) decides trips, holds, stages and releases from observed events. A thin OpenCode V2 server plugin (`index.ts`) feeds it hook and event data, applies its decisions through `permission.evaluate` and `tool.execute.before`, mirrors state to `ctx.storage`, and fails open. Policies 01 and 03 change first, so the gate implements the new rule.

**Tech Stack:** TypeScript run by OpenCode's embedded runtime and by `node --test` (types stripped); Python 3.11 standard library for the feature manifest and test wrapper; Markdown policies.

**Spec:** `docs/superpowers/specs/2026-11-12-opencode-escalation-gate-design.md`

## Global Constraints

- OpenCode V2 server plugin API; host OpenCode 2.2.6. No `experimental` API.
- No dependencies, no package manager, no build step.
- Plugin directory: `runtime/adapters/opencode_workflow/` with `index.ts` (OpenCode loads a directory's `index`). This replaces the spec's single file `runtime/adapters/opencode_workflow.ts`, because a `plugins` path entry loads a directory.
- The routing shim `runtime/adapters/opencode_plugin.ts` is not modified; the gate may import its exported pure helpers (`routeFacts`, `derivedSession`).
- Feature `escalation_gate`: default `{"enabled": false}`, control `settings`, consent `none`, settings `repair_rounds` (integer, default `2`), `check_patterns` (array), `ladder` (array, default `[]`).
- Escalation limit `3`; one re-evaluation reset per file.
- Variant order `low`, `medium`, `high`, `xhigh`, `max`.
- No model calls, no output and no notices while nothing is held. The plugin never logs prompts, diffs, check output or messages.
- No commercial model names in policies, client contracts or messages. Tests may use fake routes.
- Any internal error allows the action.
- Each Python file stays at or under 1000 lines.

## Review Focus

- Two subagents of one parent finishing close together: each finish is attributed to its own session, and only the stronger, edit-free one created after the hold releases.
- Edit resources that are relative, `..`-prefixed or absolute: all normalize to one absolute key per file, so the same file is never counted twice.
- A child session prompt containing "reset the counter" never resets anything; only root-session user prompts do.
- A command that merely mentions a check (`rg pytest src`, `cat scripts/validate.py`) is not a check run.
- A feature-status child that hangs or is missing: setup finishes within its 5 s bound and the gate stays inactive.

---

### Task 1: Policy changes

**Files:**
- Modify: `policies/01-project-scope.md:40` (the "Ask only when" paragraph)
- Modify: `policies/03-agent-delegation.md:134-145` (stuck-pattern escalation)
- Modify: `docs/policy-sources.md:590` and `:616` (coverage rows)
- Modify: `docs/escalation-validation.md` (new dated section)
- Regenerate: the prepared instruction map

**Interfaces:**
- Produces: the policy text the gate's client contract (Task 4) points to; the anchor `policies/03-agent-delegation.md#stuck-pattern-escalation` stays unchanged.

- [ ] **Step 1: Replace the first paragraph sentence in Project Scope**

Replace line 40 with the approved "After" text in the spec's "Project Scope: requirements as the hard gate" section, verbatim.

- [ ] **Step 2: Rewrite the stuck-pattern ladder in Agent Delegation**

Apply the spec's "Agent Delegation: stuck-pattern escalation" list, keeping every other requirement in steps 1–6:
- step 2 gains: escalation within authorized options needs no user approval; from the second escalation on a target, the brief also asks whether the design itself is the cause;
- step 4: "at most three times per target"; when the third escalation does not converge or no more capable authorized option remains, run the architecture re-evaluation (input, performer, question, independent review, coordinator acceptance within the agreed requirements, user question only when one would change); an accepted re-evaluation resets the count once per target; after the reset, stop and ask the user when three more escalations do not converge, no more capable option remains, or an original limit would be exceeded; budgets never reset;
- step 5's decidable request also lists the re-evaluation's outcome.

Do not add the OpenCode gate pointer yet (Task 4).

- [ ] **Step 3: Update the source records**

In `docs/policy-sources.md:590`, change "at most five escalations" and "after the fifth escalation" to the new ladder, add the re-evaluation, the one-time reset, the second-escalation design question and the no-approval rule, and add "user request, 12 November 2026; design `superpowers/specs/2026-11-12-opencode-escalation-gate-design.md`" to the source column. In `:616`, add the requirements-as-hard-gate rule to the Project Scope conflicts row with the same source.

- [ ] **Step 4: Walk representative tasks and record them**

Add a section "12 November 2026: three escalations and re-evaluation" to `docs/escalation-validation.md` with one table row per walk, in the existing table's format:
- third escalation fails, a simpler design keeps every agreed requirement: coordinator accepts after independent review, count resets once;
- the simpler design would drop an agreed requirement: user question;
- second exhaustion after the reset: decidable request to the user;
- first escalation: no design question; second: design question present;
- the agent is about to ask permission for a stronger diagnosis: the no-approval rule applies;
- an unrelated single new defect: no stuck pattern (unchanged row still holds).

For each walk, confirm that the policy index loads Delegation and Project Scope for that trigger, and that no unrelated contract is required.

- [ ] **Step 5: Regenerate and validate**

Run: `python3 -B scripts/read_instructions.py --prepare` then `python3 -B scripts/validate.py`
Expected: `"validation": "passed"`; only the existing bundle-size warning.

- [ ] **Step 6: Commit, then run affected tests**

```bash
git add policies/01-project-scope.md policies/03-agent-delegation.md docs/policy-sources.md docs/escalation-validation.md <regenerated map>
git commit -m "docs(policies): requirements as the hard gate; three escalations and re-evaluation"
```

Run: `python3 -B scripts/test_sharded.py test_prepared_instructions test_startup test_onboarding test_changes test_package test_package_manifest`
Expected: all pass. A changed policy digest is expected for legacy enrollment records; tests that pin it use fixture packages.

---

### Task 2: Gate state machine

**Files:**
- Create: `runtime/adapters/opencode_workflow/gate.ts`
- Test: `tests/opencode_workflow/gate.test.ts`

**Interfaces:**
- Produces (all exported from `gate.ts`):
  - `type Route = { provider: string; model: string; variant: string | null }`
  - `type GateSettings = { repairRounds: number; checkPatterns: string[]; ladder: string[] }`
  - `type Decision = { effect: "allow" } | { effect: "deny" | "ask"; message: string }`
  - `type Stage = "escalating" | "reevaluation" | "stop"`
  - `type GateStatus = { held: { file: string; rounds: number; stage: Stage; escalations: number; levels: Route[] }[]; resetUsed: string[]; lastError: string | null }`
  - `type GateSnapshot` (JSON-serializable; opaque to callers)
  - `const DEFAULT_SETTINGS: GateSettings` with `repairRounds: 2`, `ladder: []`, `checkPatterns: ["scripts/validate.py", "scripts/test_sharded.py", "pytest", "unittest", "node --test", "npm test", "pnpm test", "yarn test", "go test", "cargo test", "make test", "make check"]`
  - `const MAX_ESCALATIONS = 3`
  - `function parseSettings(values: unknown): GateSettings | null`
  - `function compareRoutes(a: Route, b: Route, ladder: string[]): 1 | 0 | -1 | null`
  - `function isCheck(command: string, patterns: string[]): boolean`
  - `function resetRequest(text: string): { files: string[] } | null` (empty `files` means every held file)
  - `class Gate` with `constructor(settings: GateSettings, snapshot?: GateSnapshot)` and methods `created(session: string, parent: string | null): void`, `routed(session: string, route: Route): void`, `edited(session: string, files: string[]): void`, `failed(session: string): void`, `finished(session: string): void`, `editDecision(session: string, files: string[]): Decision`, `followUpDecision(caller: string, target: string): Decision`, `isRoot(session: string): boolean`, `reset(session: string, request: { files: string[] }): string[]`, `status(session: string): GateStatus`, `snapshot(): GateSnapshot`

Semantics the tests pin (one monotonic event sequence per gate; files are absolute paths supplied by the caller):
- A tree is a root (parent `null`) and every session created under it. Derived-shape sessions (`ses_` + 32 lowercase hex) and their children are ignored.
- `failed` and `finished` of an edit-free child set the tree's last failure. `finished` evaluates releases first and records the failure after, so the first repair after a release is round 1 of the new level. Finishes of roots are ignored.
- `edited`: a re-edit of `f` after a failure newer than both `f`'s last edit and its last round adds one round and records the editing session's route as a level. First edits never count.
- Held: rounds ≥ `repairRounds` and a failure newer than the latest round; the hold time is that failure.
- `finished(child)` releases each held file whose hold is older than the child's creation, when the child made no edits and its route is:
  - stage `escalating`: stronger (`compareRoutes === 1`) than every level → rounds `0`, level appended, escalations `+1`;
  - stage `reevaluation` (escalations ≥ 3, or no ranked option above every level, and reset unused): at least as strong (`≥ 0`) as every level → escalations `0`, rounds `0`, reset used.
- Stage `stop`: reset used and (escalations ≥ 3 or no ranked option above). `editDecision` and `followUpDecision` return `ask`; other held stages return `deny`.
- "No ranked option above": the strongest level is at `max` and no ladder entry after its model (or its model is absent from the ladder).
- `followUpDecision(caller, target)` refuses when `target` made a round on a held file of the caller's tree.
- `reset` (user override) deletes the named files' entries, or every held file's when `files` is empty, and returns the cleared files.
- Messages begin `escalation gate:` and name at most 5 files (relative display is the plugin's job), the round count, the stage, the required next step for that stage and `policies/03-agent-delegation.md#stuck-pattern-escalation`. No model names.
- Bounds: 64 trees (oldest evicted), 512 files and 4096 sessions per gate; beyond them, untracked work is allowed.

- [ ] **Step 1: Write the failing tests**

In `tests/opencode_workflow/gate.test.ts`, with `R = (model, variant) => ({ provider: "p", model, variant })` and a root `ses_root`, child sessions `ses_a`, `ses_b`, `ses_d`, files `/w/a.ts`, `/w/b.ts`:

```ts
test("first edits and one fail-fix-pass loop never hold", ...)        // edited, failed, edited → editDecision allow
test("holds after two rounds and a new failure", ...)                 // edit, fail, edit, fail, edit, fail → deny, message matches /^escalation gate:/ and /a\.ts/
test("passing checks never clear rounds", ...)                        // no event clears rounds except release or reset
test("holds edits in children of the same tree", ...)                 // editDecision("ses_a", ["/w/a.ts"]).effect === "deny"
test("refuses a follow-up to a session that made a round", ...)       // followUpDecision("ses_root", "ses_a").effect === "deny"
test("a same-level reviewer never releases", ...)                     // child on the same route finishes → still deny
test("a stronger edit-free child created after the hold releases", ...)
test("a stronger child created before the hold does not release", ...)
test("a child that edited does not release", ...)
test("concurrent children: only the stronger one releases", ...)      // Review Focus
test("three escalations lead to re-evaluation, accepted at equal rank", ...)
test("re-evaluation resets once; the next exhaustion asks", ...)      // effect === "ask"
test("no ranked option above goes to re-evaluation, then asks", ...)  // R("m", "max") with ladder []
test("ranking: ladder order, then variants; unranked never counts", ...) // compareRoutes cases, incl. null
test("user reset clears named files or every held file", ...)
test("resetRequest: phrases, files and negation", ...)                // "reset the counter for a.ts" → {files:["a.ts"]}; "don't reset the gate" → null; "reset" alone → null
test("isCheck: segments, flags and viewers", ...)                     // "cd x && python3 -B scripts/validate.py" true; "rg pytest src" false; "cat scripts/validate.py" false
test("derived-shape sessions are ignored", ...)
test("snapshot round-trips a hold", ...)
test("parseSettings: defaults, valid values, invalid returns null", ...)
```

Each test body asserts the `effect` (and for releases the next `editDecision`) after the listed event sequence.

- [ ] **Step 2: Run to verify they fail**

Run: `node --test tests/opencode_workflow/gate.test.ts`
Expected: FAIL, module `runtime/adapters/opencode_workflow/gate.ts` not found.

- [ ] **Step 3: Implement `gate.ts`**

Plain classes and `Map`s; no Node imports beyond types. `isCheck` splits on `&&`, `||`, `;`, `|`, drops leading `NAME=value` tokens, rejects segments whose first token is one of `grep`, `rg`, `cat`, `echo`, `sed`, `head`, `tail`, `less`, `git`, and matches when the pattern's whitespace-split tokens appear as a contiguous run of the segment's tokens. `resetRequest` requires the word `reset` and one of `gate`, `counter`, `counters`, `hold`, `holds`, `escalation`; it returns `null` when `don't`, `do not`, `never` or `no` precedes `reset` in the same sentence; files are tokens matching `[\w./-]+\.[A-Za-z0-9]+`.

- [ ] **Step 4: Run to verify they pass**

Run: `node --test tests/opencode_workflow/gate.test.ts`
Expected: all pass, `# fail 0`.

- [ ] **Step 5: Commit**

```bash
git add runtime/adapters/opencode_workflow/gate.ts tests/opencode_workflow/gate.test.ts
git commit -m "feat(opencode): escalation gate state machine"
```

---

### Task 3: Plugin wiring

**Files:**
- Create: `runtime/adapters/opencode_workflow/index.ts`
- Test: `tests/opencode_workflow/plugin.test.ts`

**Interfaces:**
- Consumes: everything Task 2 exports; `routeFacts(model: unknown)` and `derivedSession(id: unknown)` from `../opencode_plugin.ts`.
- Produces:
  - `function createWorkflowPlugin(config?: { featureStatus?: (directory: string) => Promise<unknown> }): { id: "vibe-workflow"; setup(ctx: any): Promise<() => void> }`
  - `export default createWorkflowPlugin()`
  - tool `escalation_gate_status` (no input; returns `JSON.stringify(gate.status(sessionID))`)
  - storage key `escalation-gate/state`
  - `function exitStatus(event: unknown): number | null`

Behavior:
- Setup reads the feature through `featureStatus`; the default runs `python3 -I -B <package>/scripts/workflow.py features status escalation_gate --cwd <ctx.location.directory>` with a 5 s timeout, where `<package>` is two directories above this file. Active only when `features.escalation_gate.effective === true` and `parseSettings(values)` succeeds. It re-reads on `config.updated`. Inactive hooks return immediately.
- Restores `Gate` from `ctx.storage.get("escalation-gate/state")`; writes the snapshot after each state change.
- Events `session.created` → `created`; `session.execution.succeeded|failed|interrupted` → `finished`; `config.updated` → re-read feature.
- `session.hook("model.request")` → `routed` with `routeFacts(event.model)` (provider, model, variant).
- `permission.evaluate`, actions `edit`, `write`, `patch`: resolve `event.resources` against `ctx.location.directory` to absolute paths; `editDecision`; on `deny`/`ask` set `event.effect` and `event.message` (only tightening: never change an existing `deny`); on allow, remember the files under `event.source.id`.
- `tool.execute.after`: for `edit`, `write`, `patch` with `status === "completed"` → `edited(session, files remembered for event.id)`; for `shell` (and `bash`) when `isCheck(input.command)` and `exitStatus(event)` is non-zero, or `status === "error"` → `failed(session)`. `exitStatus` reads `result.metadata.exit`, then `result.metadata.exitCode`; anything else is `null` (not a failure).
- `tool.execute.before` for `subagent` with `input.sessionID`: `followUpDecision`; a refusal throws `Error(message)`; `ask` calls `ctx.permission.assert` with action `escalation_gate`.
- `session.hook("prompt")`: only when `gate.isRoot(sessionID)` (or `ctx.session.get` reports `parentID == null`); `resetRequest(event.prompt.text)` → `reset` → append `\n\n[escalation gate: counter reset by user for <files or "all held files">]` (or `[escalation gate: nothing to reset]`) to `event.prompt.text`. This replaces the spec's instruction-entry confirmation, which is an experimental API.
- Every hook body is wrapped: an exception records `lastError` (a fixed reason, never payload) and allows.

- [ ] **Step 1: Write the failing tests**

Reuse the shape of `fakeCtx` in `tests/opencode_plugin/plugin.test.ts:67-130`, adding `storage` (in-memory), `tool.transform`, and `permission.assert`. `featureStatus` is injected.

```ts
test("inactive when the feature is off, missing or unreadable", ...)
test("setup finishes within the bound when feature status hangs", ...)   // Review Focus; injected never-resolving promise, setup resolves ≤ 5.5 s
test("trip and hold end to end through hooks", ...)                     // permission + execute.after + shell failures → evaluate sets effect "deny"
test("a configured deny is never changed", ...)
test("denied edits are not counted", ...)
test("resources normalize to one absolute file", ...)                   // Review Focus: "a.ts", "./a.ts", "../w/a.ts" from /w/sub, "/w/a.ts"
test("subagent follow-up refusal throws the gate message", ...)
test("execution events release on a stronger child", ...)
test("root prompt resets and appends the note; child prompt does not", ...) // Review Focus
test("status tool reports held files and lastError", ...)
test("state survives a reload through storage", ...)
test("hook errors allow and record lastError", ...)
test("exitStatus reads metadata.exit and metadata.exitCode only", ...)
test("nothing is written to the prompt or messages while nothing is held", ...)
```

- [ ] **Step 2: Run to verify they fail**

Run: `node --test tests/opencode_workflow/plugin.test.ts`
Expected: FAIL, module `runtime/adapters/opencode_workflow/index.ts` not found.

- [ ] **Step 3: Implement `index.ts`**

Use `node:child_process` `execFile` for the default `featureStatus`, `node:path` for resolution. Register hooks with the `domain.hook(name, callback)` calls the shim uses.

- [ ] **Step 4: Run to verify they pass**

Run: `node --test tests/opencode_workflow/plugin.test.ts tests/opencode_workflow/gate.test.ts`
Expected: all pass, `# fail 0`.

- [ ] **Step 5: Commit**

```bash
git add runtime/adapters/opencode_workflow/index.ts tests/opencode_workflow/plugin.test.ts
git commit -m "feat(opencode): escalation gate plugin wiring"
```

---

### Task 4: Feature, contract and package wiring

**Files:**
- Create: `features/escalation_gate.json`
- Create: `clients/opencode-escalation-gate.md`
- Create: `tests/test_opencode_workflow.py`
- Modify: `clients/index.md` (one table row)
- Modify: `policies/03-agent-delegation.md` (one pointer line at the end of stuck-pattern escalation)
- Modify: `docs/policy-sources.md` (one row for the gate)
- Modify: `tests/test_features.py:468-471` (`ShippedManifestTests`)
- Regenerate: the prepared instruction map

**Interfaces:**
- Consumes: plugin directory and status tool from Task 3; policy anchor from Task 1.
- Produces: the feature `escalation_gate`, its procedure `clients/opencode-escalation-gate.md`, and the install entry `"plugins": ["<package>/runtime/adapters/opencode_workflow"]`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_features.py` `ShippedManifestTests`:

```python
def test_escalation_gate_manifest(self):
    gate = features.manifests()["escalation_gate"]
    self.assertEqual((gate["default"], gate["control"], gate["consent"]), ({"enabled": False}, "settings", "none"))
    self.assertEqual(gate["settings"]["repair_rounds"], {"type": "integer", "default": 2})
    self.assertEqual(gate["procedure"], "clients/opencode-escalation-gate.md")
```

`tests/test_opencode_workflow.py` runs `node --test --test-reporter=tap` on both suites like `tests/test_opencode_plugin.py:118-137` (skip without `node`; assert `# fail 0` and `# skipped 0`).

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -B scripts/test_sharded.py test_features test_opencode_workflow`
Expected: `test_escalation_gate_manifest` fails with `KeyError: 'escalation_gate'`; the node wrapper passes.

- [ ] **Step 3: Add the manifest, contract and pointers**

- `features/escalation_gate.json`: fields per `clients/features.md`, `summary` "Holds repeated same-level repairs on OpenCode until a stronger diagnosis runs.", `skills: []`, `scopes: ["project"]`, `requires: []`, settings `repair_rounds` `{"type": "integer", "default": 2}`, `check_patterns` `{"type": "array", "default": <DEFAULT_SETTINGS.checkPatterns>}`, `ladder` `{"type": "array", "default": []}`.
- `clients/opencode-escalation-gate.md`: what it observes, what it holds and releases, stages, reset by the user's message, troubleshooting (status tool, hold messages), install entry, limits (shell writes, `!cmd`, other plugins, server API, fail-open), and "Package-tested only; live probes pending" until Task 5. No model names.
- `clients/index.md`: `| [OpenCode escalation gate](opencode-escalation-gate.md) | You install, configure, test or diagnose the optional OpenCode stuck-pattern escalation gate. |`
- `policies/03-agent-delegation.md`: after step 6, "On OpenCode, the optional [escalation gate](../clients/opencode-escalation-gate.md) holds repeated same-level repairs; it supports this procedure and does not replace it."
- `docs/policy-sources.md`: a row for the gate contract, sourced from the design doc and enforced by `runtime/adapters/opencode_workflow/` with its tests.

- [ ] **Step 4: Regenerate, validate and run tests**

Run: `python3 -B scripts/read_instructions.py --prepare`, then `python3 -B scripts/validate.py`
Expected: `"validation": "passed"`.

Commit, then run: `python3 -B scripts/test_sharded.py test_features test_opencode_workflow test_opencode_plugin test_prepared_instructions test_package test_package_manifest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add features/escalation_gate.json clients/opencode-escalation-gate.md clients/index.md policies/03-agent-delegation.md docs/policy-sources.md tests/test_features.py tests/test_opencode_workflow.py <regenerated map>
git commit -m "feat(opencode): escalation gate feature, contract and package wiring"
```

---

### Task 5: Live probes (needs the user's approval of a subscription-only budget)

**Files:**
- Create: `tests/fixtures/opencode_workflow/probes.json` (sanitized key sets and observed values only)
- Modify: `runtime/adapters/opencode_workflow/index.ts` only where a probe contradicts an assumed field
- Modify: `clients/opencode-escalation-gate.md` (qualification status)

**Interfaces:**
- Consumes: the installed plugin from Task 4, loaded in a disposable project outside the repository.

- [ ] **Step 1: Ask the user for the budget** (number of requests, route, time limit); proceed only after approval.
- [ ] **Step 2: Observe on OpenCode 2.2.6, recording key sets, never content:**
  - `tool.execute.after` for `shell`: where the exit status is;
  - `permission.evaluate` for `edit` from a child carries the child's `sessionID` and a `source.id` matching `tool.execute.after`'s `id`;
  - a `deny` message reaches the model and the file is unchanged;
  - a `subagent` follow-up carries `input.sessionID`;
  - `model.request` reports each child's route;
  - the `prompt` hook fires for root prompts and whether it fires for subagent prompts;
  - `session.execution.*` events carry the child `sessionID`;
  - `ctx.storage` survives a plugin reload.
- [ ] **Step 3: Fix contradicted assumptions** with a failing unit test first, then rerun `test_opencode_workflow`.
- [ ] **Step 4: Record results** in `probes.json` and the client contract; commit.

### Task 6: Behavior evaluation (needs separate approval)

Run the spec's "Behavior evaluation" cases (recurrence, persistence through re-evaluation, unrelated failures in other and the same files) under a user-approved budget; record outcomes and false-trip cost in the client contract. Report it separately from package checks and probes.

---

## Not in this plan

- The routed judge and its `unrelated` auto-clear: a separate plan once Smart Routing can prepare, dispatch and settle an interactive routed task.
- Primary-checkout guard, merge-readiness gate, review-independence verification.
