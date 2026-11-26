# Title Sync Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover eligible stale idle titles and advance an existing meter within a long turn, without polling or rewriting titles when disabled.

**Architecture:** Keep Title Sync's per-session queue and current-location owner. Reuse Git facts and the existing task records; use step events for local progress and a project-scoped, process-local relay for rare merges and preference changes. Each new idle write requires positive activity evidence.

**Tech Stack:** Node ES modules, Node 22+ `node --test`, OpenCode V2 (native qualification on 2.3.2), Git, and a test-only `gh` shim.

**Spec:** `docs/superpowers/specs/2026-11-25-title-sync-follow-up-design.md` (authority; read first).

**Approved execution scope:** Tasks 1–3. The OpenCode 2.3.2 model-free lab qualified shared preference fencing
but found no shell-list read in the plugin context. The user deferred Tasks 4–5 and their live checks.
Onboarding #399 changed the Title Sync setting to be checkout-local; preference fencing follows that scope.

## Global Constraints

- No polling, new runtime model calls, webhooks, per-tool Git checks, or mid-turn GitHub calls for progress.
- Keep the existing artifact triggers (no finding 9), normal turn-end `gh` gating, the meter's stages, and the existing `WAITING` rules.
- Coordinate only within one OpenCode server. No cross-process lock, persistent routing state, new dependencies, or public settings.
- Preserve a single owner and queue; recheck ownership, generation, preference, task identity, and the latest title before new writes. A sent write cannot be recalled.
- Never start a first meter at tool or step time, title a child, regress cells within a task, or write unchanged state.
- No README or injected-note change. No install, activation, publishing, or inference without authorization covering that action. The earlier one-turn lab approval is spent.

## File Map

- `src/session-titles.js`: task lifecycle, owner registry and preference fence, progress queue, merge routing, idle recovery.
- `src/session-facts.js`: reusable local Git and plan facts, PR identity and one bounded lookup. Do not change `src/pr-advice.js`.
- `index.js`: publish Title Sync preference saves through the onboarding wiring without changing its tool schema.
- `tests/plugin/session-titles.test.js`, `tests/plugin/session-facts.test.js`, `tests/plugin/onboarding.test.js`: extend existing one- and two-copy fakes, real Git and `gh` recorder, and onboarding integration fixtures.
- No new runtime files or manifest entries. Native qualification artifacts stay in a private lab. Tear them down after each run.

## Review Focus

- A user renames the chat **after** a new task binds: do not replace that rename (`new-task rename wins`, Task 1).
- An evaluation or merge lookup started while on resumes after off→on: it must not publish an old result (`off-on fences old writes`, Tasks 2 and 4).
- A reverted or escaping snapshot path, or an obsolete plan, cannot permanently advance the meter (`bad hints do not earn cells`, Task 3).
- The same PR number in a different repository, or a target that moves during verification, must not advance a foreign title (`verified merge routes only to the current owner`, Task 4).
- A current background shell started before a copy loaded, or an unreadable checkout during cleanup, must not be treated as absent (`unknown activity or folder is not idle proof`, Tasks 4–5).

---

### Qualification gate: prove host assumptions before using them

- [ ] Confirm the local baseline in the isolated worktree: `node --test "tests/**/*.test.js"`, `python3 -B -m unittest discover -s tests -t .`, `python3 -B scripts/validate.py`. Expect no failures. If the installed-version test fails, follow the repository's `--injection` exception and stop if that check fails.
- [ ] Obtain approval for a private lab activation. With a throwaway repository and linked worktree, two loaded copies, and a local `gh` only, prove they share a project registry and that publishing off reaches both. Prove `ctx.session.list({ directory, search, limit, order })` returns only that directory and a reusable cursor. Exercise `worktree.updated` on native removal and record whether the target owner survives delivery. No new model turn needed for these checks.
- [ ] Establish what the supported plugin surface can prove about a root, its children, and shells running before a copy activates. Start a controlled lab shell without a model if supported; show it blocks an idle decision, and that a read failure differs from an empty result. Inspect whether an earlier idle timestamp survives into a reused session's next execution; check it at an actual bind during authorized product-level qualification. Do not use private database reads or production sessions as the product mechanism.
- [ ] Record observed vs unavailable behavior. Missing shared routing blocks cross-copy merge and disable fencing; missing scoped listing or positive idle evidence blocks the corresponding idle writes. A missing removal event permits only activation/turn-end fallback. If a scope reduction is needed, obtain approval rather than marking the finding fixed. A later section covers product-level live tests, with separate inference consent.

### Task 1: Keep or drop a whole task and scope renames to it

**Files:** Modify `src/session-titles.js` (`restore`, `load`, `save`, `bind`, `evaluate`); test `tests/plugin/session-titles.test.js`.

**Interfaces:** Keep the storage key `session-titles/<sessionID>`. Add optional `task.resetFrom: string | null` for a fresh task replacing a known prior title. A complete record contains `task`, `last`, and `silenced`; a missing or invalid task never restores a standalone silence flag.

- [ ] **Write failing tests.** Use `host(t)` with a second spec `docs/superpowers/specs/2026-11-26-ship-search-design.md`. Exercise a same-task plan, replacement spec, completion after a rename, and eviction. Reuse the existing real owner and title-update counters. The key assertions are:

  ```js
  // same-task plan preserves rename; a distinct spec only retitles at turn end
  assert.equal(h.titles.get("ses_1"), "My own title")
  assert.equal(h.titles.get("ses_1"), compose("ship search", 1, true))
  // new-task rename wins (rename after bind); known completion leaves no receipt
  assert.equal(h.titles.get("ses_1"), "New manual name")
  assert.equal(h.storage.data.has("session-titles/ses_1"), false)
  ```

  Cover a new plan-only bind after terminal state; a still-silenced old task superseded by a distinct spec without a PR read to establish its end; an evicted record and a legacy taskless silence record; a never-used session retaining its normal initial topic.
- [ ] **Run red:** `node --test tests/plugin/session-titles.test.js`. Expect the reset assertions to fail against session-wide silence and the known-terminal rename to leave a record.
- [ ] **Implement.** Replace the full entry only on today's new-task triggers: different spec, or a plan with no active task. Mark the old title for replacement if the task is known to be reused (prior binding, owned prefix, or a qualified prior-turn timestamp). Use the new slug at the first meter only if the previous title is still current. Otherwise honor a rename made after binding; do not mistake first-turn generated titles for a previous task. Retire the full entry on known completion even if the final write is skipped for a user rename, and prune the full entry at the existing cap. Do not poll or add PR reads for silenced tasks.
- [ ] **Run green:** `node --test tests/plugin/session-titles.test.js`; expect all pass, including unchanged storage, child binding, and plan adoption.
- [ ] **Commit:** stage the two named files; run `git diff --cached --check`; commit `fix: scope title renames to the active task`.

### Task 2: Fence queued writes when Title Sync goes off

**Files:** Modify `src/session-titles.js` (`registerSessionTitles`, queue/current guards) and `index.js` (session-title settings wrapper); test `tests/plugin/session-titles.test.js` and `tests/plugin/onboarding.test.js`.

**Interfaces:** `registerSessionTitles(...)` additionally returns `preferenceSaved(enabled: boolean): void`, a no-op when unregistered. Use the host-qualified project key and canonical owner directory. A project entry holds owners and a preference revision; the current owner can later accept merge jobs. `enqueue` supplies a token containing session generation and project revision, captured on admission.

- [ ] **Write failing tests.** In `world(t)`, block one copy's fact/session read and another's already-started update. Save off through the peer, release reads, then test off→on before release as a separate case. In the existing onboarding `fixture`, give the host real fake hook/event registration and prove calling apply after a fresh preview publishes before it returns `saved`. Preview, rejected apply and `unchanged` do not publish.

  ```js
  // off-on fences old writes; unchanged titles and data are left alone
  assert.equal(oldCopy.calls.update.length, writesAlreadySent)
  assert.equal(w.storage.calls.scan, scansBeforeOff)
  assert.equal(w.title("ses_1"), titleBeforeOff)
  ```

  Assert no new session list while off (including activation), no new spec/plan binding, and no fence against another project. After a successful enable, an ordinary **new** event can update. Add a move while queued and close of one peer; no stale writes and no registry entry for that copy.
- [ ] **Run red:** `node --test tests/plugin/session-titles.test.js tests/plugin/onboarding.test.js`. Expect queued or cross-copy fencing assertions to fail.
- [ ] **Implement.** Register only after successful setup and remove only the registered instance on close. Scope by verified project identity, not shell cwd. Wrap `sessionTitles.apply` in `index.js` and notify synchronously on `state === "saved"` using the saved boolean, before returning. Preserve native permissions and return values. Check the revision at each title write and save boundary. Carry the original revision through child-to-root routing; a queued root job must not renew the child's admission. Retained work cannot start new writes after off is published or pass an off→on. No title or storage scan on disable.
- [ ] **Run green:** repeat the red command; expect all pass. Also check success when the host cannot register title hooks.
- [ ] **Commit:** stage the four named files; check diff; commit `fix: leave existing titles alone when disabled`.

### Task 3: Verify plausible progress steps with local Git

**Files:** Modify `src/session-facts.js` (`readFacts`, extract `readPlanPaths`) and `src/session-titles.js` (evaluation and step routing); test `tests/plugin/session-facts.test.js` and `tests/plugin/session-titles.test.js`.

**Interfaces:** Add `readFacts({ ..., localOnly = false })`, keeping the facts shape and exiting before any `gh` call when local-only. Add `readPlanPaths(plan: string): Promise<string[]>` using existing `declaredPaths` (throw on read failure). In the fake host add `step(sessionID, directory, files)` emitting `session.step.ended` with `location: { directory }` and `data: { sessionID, files }`.

- [ ] **Write failing tests.** In a real pushed Git checkout, confirm local-only facts still report the changed tracked and untracked files and declarations but no `gh`. In `world(t)` give the task four declared paths; let a root and same-checkout child change two and then four. Gate one local check so a later higher milestone is not lost while it runs.

  ```js
  assert.equal(w.title("ses_1"), compose("Fix login flow", 3, true))
  assert.equal(w.title("ses_1"), compose("Fix login flow", 4, true))
  assert.equal(ghCallsAfterSteps, ghCallsBeforeSteps)
  ```

  `bad hints do not earn cells`: supply duplicates, a reverted path, malformed or escaping paths, an unrelated session and a subfolder/other checkout; assert no inappropriate write or Git check. Verify failed status/diff, missing checkout, no cache on cold reload, no first or silenced title, and no repeated check for an attempted milestone. A running request's `WAITING` is unchanged.

  Adopt the main owner's task into the linked worktree by a plan write. Deliver the linked checkout's child step to both copies. Assert exactly one Git check in the linked checkout and exactly one title update by the main owner. Edit the plan twice in one root turn, with the second edit landing during a checked milestone. Assert the declaration read budget stays at one, old-plan hits earn no cells, and the turn-end check picks up the new plan.
- [ ] **Run red:** `node --test tests/plugin/session-facts.test.js tests/plugin/session-titles.test.js`; expect local-only and pre-turn-end advancement tests to fail.
- [ ] **Implement.** Keep a transient cache per task of declarations, last confirmed matches, distinct relevant hints, and attempted milestones. Match only the known checkout root and valid POSIX repo-relative paths. Resolve a bounded parent chain only after a relevant candidate checkout is found. Use the existing queue; at most one pending progress job per owner/root, and reconsider new hints for a higher unattempted stage once it settles. Invalidate declarations, hits, and in-flight plan credit on **every** observed plan edit. After one declaration reread, defer further declaration-based progress to turn end; never reset the milestone budget on a plan edit. Reset hints/budget at the root's turn boundary and discard state on task changes, move, eviction, or close. Verify stages 2–4 from local facts only; if plan evidence is invalid or failed, advance only on positively verified non-doc changes. Preserve the current topic and `WAITING`. Do not alter turn-end PR or diagnostics.
- [ ] **Run green:** repeat the red command; expect all pass and no baseline turn-end read/write count increase.
- [ ] **Commit:** stage the four named files; check diff; commit `feat: advance Title Sync between tool steps`.

### Task 4: Route a verified numbered merge to the current owner

**Files:** Modify `src/session-facts.js` and `src/session-titles.js`; test `tests/plugin/session-facts.test.js` and `tests/plugin/session-titles.test.js`. Prerequisite: qualification gate for shared routing and positive idle evidence.

**Interfaces:** `readFacts(...).pr` adds an optional validated `url`. `readMergedPr({ cwd, number, run = defaultRun }) → Promise<string | null>` calls `gh pr view <number> --json url,state` with the existing 5,000 ms limit. `task.pr` is null or `{ url, head, merged: boolean }`; it belongs to that binding and checkout. Internal `proveIdle(sessionID, { shellSnapshot } = {}) → Promise<boolean>` returns true only from bounded positive root/child/shell evidence; use the existing activity limits and reject incomplete reads. Internal owner job `finishVerified(sessionID, expected, admitted) → Promise<void>` compares the expected `{ bound, top, url }`, then applies the verified terminal result or retains same-task evidence if cleanup is still needed.

- [ ] **Write failing tests.** Extend the fake `gh` recorder with `pr view` and explicit command metadata. Run `gh pr merge 7 --merge` from a separate chat in another checkout with the owning copy loaded. Use `https://github.com/acme/example/pull/7` for the tracked URL, and `https://github.com/elsewhere/example/pull/7` for the collision.

  ```js
  assert.equal(w.title("ses_target"), compose("Fix login flow", 5, false))
  assert.equal(w.record("ses_target"), undefined)
  assert.deepEqual(viewCalls, [["pr", "view", "7", "--json", "url,state"]])
  ```

  `verified merge routes only to the current owner`: a move before verification returns routes to the destination; a move during the owner's queued check prevents that owner's write. Block `pr view`, publish off then on, and release the call; assert no owner title or storage write. Give a background result the same command, completed status, exit `0`, and a `shellID`; assert zero scans and lookups.

  Assert mismatched or missing URLs, `OPEN`, bad output, failure, nonzero or missing exit, ambiguous command, implicit branch switch, cross-project cwd, and no candidate do not advance. Skip a busy, unmarked, silenced or cold target. Running/unknown activity blocks completion. Keep cell 4 with required cleanup while the worktree exists. Rebind, adopt a checkout with the same branch name, and switch branches: each clears the old identity; a failed branch read preserves it. A legacy remembered-branch result cannot reattach an identity cleared by a confirmed switch.
- [ ] **Run red:** `node --test tests/plugin/session-facts.test.js tests/plugin/session-titles.test.js`; expect PR-identity and cross-copy refresh failures.
- [ ] **Implement.** Extend only the existing `pr list` fields with `url`; retain existing branch selection, date checks and call frequency. Check the qualifying head before retaining identity; clear on rebind, adoption, and confirmed branch switch, not on a failed read. Accept a simple `gh` or `rtk gh` command with a positive decimal PR argument and supported flags. Require the observed foreground envelope: completed hook and shell metadata, numeric exit `0`, no `shellID` or other background marker. Capture the original project revision at hook entry and keep it across scan, verification, and owner enqueue. Scan `session-titles/` once per accepted signal, at most 128 records; verify the directory's shared Git directory and make at most one `pr view` if another eligible root is tracked. Compare exact verified URLs and enqueue through the current owner without blocking the tool. Revalidate all guards before publishing; treat only `ENOENT` as proof of removal. No new writes from an unloaded owner or stale project revision.
- [ ] **Run green:** repeat the red command; expect all pass and a single lookup even with several candidates for PR 7.
- [ ] **Commit:** stage the four named files; check diff; commit `fix: refresh idle titles after verified merges`.

### Task 5: Recover locally at owner activation and on verified cleanup

**Files:** Modify `src/session-titles.js` (setup, subscription, `evaluate`, reuse idle/finish helpers); test `tests/plugin/session-titles.test.js`.

**Interfaces:** Reuse Task 4's `proveIdle` and `finishVerified`; internal `recoverAtActivation() → Promise<void>` runs once after subscribing, only while enabled. Listen for `worktree.updated.data.projectID` only if native delivery qualifies.

- [ ] **Write failing tests.** Seed before `registerSessionTitles` so the one activation pass can see records. With `started = 10_000`, an eligible root with `busy: 9_999` and no later idle relabels; a current run (`busy: 10_001`), a later idle timestamp, a running child or shell, and unreadable activity do not. Drive a verified merge awaiting cleanup through removal and a worktree event, then through owner activation without the event.

  ```js
  assert.equal(w.title("ses_stale"), compose("Fix login flow", 2, true))
  assert.equal(w.title("ses_shell"), compose("Fix login flow", 2, false))
  assert.equal(activationLists.length <= 4, true)
  assert.equal(ghCallsDuringRecovery, 0)
  ```

  `unknown activity or folder is not idle proof`: a lifetime-crossing shell, malformed host response or non-`ENOENT` failure does not clear the gate. Include wrong-directory results, fresh/finished/silenced controls, a preference change or unload mid-list, and an unrelated project update. In a separate fixture keep the owner loaded, remove the worktree with no event, fail the normal `gh` read, and end the next turn. Assert cell 5 and task retirement from retained evidence, with no extra `gh` call.
- [ ] **Run red:** `node --test tests/plugin/session-titles.test.js`; expect activation and cleanup recovery to fail.
- [ ] **Implement.** Start the pass after registration without holding setup open. Request exact `directory`, `search: MARK`, `limit: 50`; follow no more than four pages, verifying ownership and root status even when returned by a filter. Reuse the process start cutoff, not setup time. Read retained tasks only for eligible incomplete marked titles. Require strict idle evidence before relabeling or finishing. Reuse the qualified shell read once per activation where possible, and recheck live activity before writes. On a matching optional worktree event attempt only already-verified merges waiting on that owner's cleanup. No Git process or GitHub lookup in either path. On an ordinary turn end with no removal event, let the same binding's retained `MERGED` evidence and positive folder removal satisfy the existing completion rules. Do not add or suppress an ordinary turn-end lookup.
- [ ] **Run green:** repeat the red command; expect all pass, including no list while off and unchanged-turn storage.
- [ ] **Commit:** stage both named files; check diff; commit `fix: recover stale idle labels locally`.

## Final Proof and Delivery

- [ ] Run `python3 -B scripts/validate.py`, `python3 -B -m unittest discover -s tests -t .`, and `node --test "tests/**/*.test.js"` on the integrated head. Export with `python3 -B scripts/package.py --output <new-private-output-dir>` and validate the export with `python3 -B scripts/validate.py --root <new-private-output-dir>`. No install.
- [ ] Obtain separate approval for any fresh lab activation or live model turns needed at this head. Reuse the approved lab isolation: throwaway checkout, fresh session, local origin and `gh` shim. Show an idle target refreshing from another copy, cleanup holding then releasing, startup/no-shell and startup/running-shell controls, disable with untouched existing title, and progress before the root's turn ends from root and descendant changes. Do not count the earlier payload-only probe as product proof. Log observed behavior versus unavailable gates and verify teardown.
- [ ] Independent behavior/security review on the full branch; resolve material findings and rerun affected checks. Present any blocked host capability or proposed scope reduction before marking ready. Use the project's PR workflow only when authorized for delivery. No release, installation, or publication as part of this plan.
