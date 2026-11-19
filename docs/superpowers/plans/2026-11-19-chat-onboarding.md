# Thin Chat Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a short OpenCode onboarding conversation that saves project preferences, defaults Smart Router to ON, and preserves non-blocking native fallback.

**Architecture:** Store portable preferences in one project-root JSON file. Register one onboarding skill and one preview/apply tool through the existing OpenCode plugin; retain the Python advisory selector and its bounded transport. Add project-aware caller instructions and tool gating, not a native-worker interceptor or replacement workflow engine.

**Tech Stack:** Existing JavaScript ES modules/Node built-ins, Python 3.11+ standard library, OpenCode V2 server APIs, Git root discovery, native question and permission controls. No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-19-chat-onboarding-design.md`

## Global Constraints

- OpenCode is the initial implementation target; Codex, Claude and other clients remain future integrations.
- Use chat and native controls. No browser wizard, custom frontend, generated policies, `AGENTS.md` rewrite or repository audit.
- Store project preferences at `<project-root>/.vibe-workflow/settings.json`; no central project registry or database.
- Smart Router defaults to ON for a new onboarding proposal. Existing saved choices win; an unanswered question is not confirmation.
- Preview changes and apply only after confirmation. Cancelling the conversation before apply writes nothing.
- Preserve unrelated settings and other plugins. Copy no credentials or account/usage caches into the project.
- An unavailable recommendation produces a brief `vibe-workflow` notice, a relevant corrective action, then the client's existing selection.
- Preserve native permission/execution denials. No plugin-driven retries, model/provider switches, worker launches or spending authority.
- Every selector result retains `admission: false`, `reservation: false`, `spend_permit: false`.
- Only available features receive questions. PR handling (#328), templates (#329), tests/builds and code/security review are not implemented here.
- Preserve existing reader/storage timeouts, account checks, failure backoff and cancellation. No quota polling, daemon or inference probe.
- Planning and implementation stay in task worktrees. No real-project onboarding, global installation/activation, push, PR, merge or inference trial is authorized by this plan.

## Review Focus

- A plugin loaded in a nested directory or linked worktree must use that checkout's root, not its primary or parent checkout.
- Existing unknown settings and edits made after preview must survive; future schemas must not be overwritten.
- A saved opt-out, absent onboarding or malformed settings must not trigger account acquisition through an explicit selector call.
- Chat confirmation must not be confused with a caller-supplied boolean or a native permission grant; denied edits remain denied.
- Fallback notices and context registration must neither leak stored data nor mutate other tools, permissions or native selection.

---

## Planning Decisions

These are proposed implementation choices, not already-shipped behavior.

### Settings and ownership

```json
{"version": 1, "smart_router": {"enabled": true}}
```

Only `version` and `smart_router.enabled` are interpreted. Preserve unknown top-level and nested fields. An existing unsupported version or invalid Smart Router shape blocks modification; never replace it with defaults. A valid version-1 document without a Smart Router choice is not yet onboarded.

Resolve the canonical checkout root using a bounded, read-only `git rev-parse --show-toplevel` from the actual plugin location, removing inherited `GIT_*` selectors. Do not use Git's common directory or the host's primary/canonical project directory. For an ordinary non-Git directory, use its canonical location. An unexpected discovery failure or timeout is `project_root_unavailable`, not permission to guess.

Resolve once per plugin-location instance. Read the bounded settings file on demand so manual edits take effect without a watcher. Project preferences are nonsecret: allow ordinary Git-restored `0644` files, unlike the existing private account caches. Reject symlinks, hardlinks, foreign ownership and group/other-writable settings paths. Do not change the private cache helper to accommodate project files.

Preview is read-only. Apply uses an exclusive, bounded lock in `.vibe-workflow/`, rechecks a SHA-256 snapshot bound to the root and original bytes, and atomically replaces only `settings.json`. Preserve the old file on failure; remove only temporary/lock entries created by this call. A pre-existing lock is `project_settings_busy`, never auto-deleted. This coordinates cooperating writers; it is not a sandbox against another authorized local writer.

### Chat entry and confirmation

Register skill ID/name `vibe-workflow-onboard`, backed by a bundled skill and procedure. Users request onboarding in chat and the caller invokes that skill. Do not invent an unsupported slash command or start a conversation during plugin setup.

Register one tool, `vibe_workflow_onboard`, with `v: 1`, `op: "preview" | "apply"`, optional boolean `enabled` on preview, and required boolean `enabled` plus `expected_snapshot` on apply. Accept no project path, client name, credentials or arbitrary configuration from model input.

The skill obtains the detected project/client, asks the approved two-option question, shows the final preview, then waits for explicit user confirmation before calling apply. Put ON first and label it as the new-project default; preserve and identify the current choice on repeat onboarding. Native question APIs need no invented `default` field.

Before apply, call the host's documented `ctx.permission.assert` for action `edit` on the exact settings path using the actual invocation context. Propagate denial; do not convert it into fallback or a successful save. Chat confirmation is an instruction-level requirement; the native edit permission is the enforced access boundary. A request field such as `confirmed: true` would not prove human consent and is not added.

Use the host's edit-resource convention: inside the checkout, a `/`-separated path relative to the plugin's actual location (including `..` when needed), not an invented permission resource. A missing permission capability blocks apply before any write. Validate denial patterns against the installed host rather than assume an absolute path is equivalent.

API source: the official [OpenCode V2 server plugin reference](https://opencode.ai/docs/build/plugins/server), specifically Tools, Skills and Permissions. Use structural registrations already supported by this package; add no SDK dependency.

### Readiness and automatic consultation

Readiness checks inspect local router configuration only. They do not start native readers or prove entitlement, current account availability or an eligible route. Display **On · Ready** with “Configuration checked; route eligibility is checked when work is delegated.” Missing/invalid configuration displays **On · Setup needed**. OFF requires no router check.

For an onboarded ON project, additive context instructs the caller to consult `smart_router_select` before delegating and use recommendations only through the client's permitted controls. This is advisory, instruction-driven integration: it does not guarantee model compliance or override client restrictions. Other tools and native `subagent` remain unchanged.

Absent, OFF or invalid project settings suppress automatic consultation and make explicit plugin selector calls return a fixed authority-free no-selection result before spawning Python. The pure Python `select` interface remains unchanged; project opt-in belongs to the plugin, not the standalone core API.

For an enabled selection failure, return an actionable `notice` alongside the existing JSON result and instruct the caller to show it in chat before continuing natively. Deduplicate identical causes per native session in a bounded, in-memory map; clear after a successful selection or opt-out. Reload may show a notice again. Add no persistent notice store.

## File Map

| File | Responsibility |
| --- | --- |
| `project.js` | New client-neutral root discovery, settings inspection, preview and conditional atomic apply |
| `onboarding.js` | New OpenCode skill/tool registration and preview/apply presentation |
| `index.js` | Reuse bounded child transport; add project gating, caller context and fallback notice handling |
| `runtime/selector.py` | Add pure local setup inspection while preserving advisory selection |
| `scripts/select.py` | Add bounded `--check-setup` mode with no reader acquisition |
| `skills/onboard-project/SKILL.md` | Short onboarding entry; load the bundled procedure |
| `skills/onboard-project/references/procedure.md` | Exact Q&A, preview, confirmation, readiness and fallback instructions |
| `tests/project.test.js`, `tests/onboarding.test.js` | New settings and onboarding behavior tests |
| `tests/plugin.test.js`, `tests/test_selector.py`, `tests/test_package.py` | Extend existing integration, setup-inspection and export proof |
| `tests/verify_cancel.mjs`, `tests/verify_native_loading.py` | Update existing opt-in verification fixtures for the new registrations |
| `package.json`, `scripts/manifest.py`, `scripts/validate.py` | Include only the four new production files and retain boundary checks |
| `README.md`, `docs/dependencies.md`, `PRODUCT.md` | Document commands, schema, limits and actual implementation status |

## Task 1: Safe Project Preferences

**Files:** Create `project.js`, `tests/project.test.js`.

**Interfaces:**
- `createProjectSettings(directory: string)` returns async `inspect()`, `preview(enabled?: boolean)`, and `apply(enabled: boolean, expectedSnapshot: string, options?: {signal})`.
- Inspection returns `{state: "not_onboarded" | "on" | "off" | "blocked", reason, root, path, enabled, snapshot}`; blocked fields may be null.
- Preview returns `{state: "preview" | "blocked", reason, root, path, previous_enabled, enabled, snapshot, change: "create" | "update" | "none"}`.
- Apply returns `{state: "saved" | "unchanged" | "blocked", reason, root, path, enabled}`. Never return the original document or unknown field values.

- [ ] Write `test_preview_defaults_on_without_writing`: a missing file proposes `enabled === true`, `change === "create"`; the directory still does not exist.

  ```javascript
  const preview = await createProjectSettings(root).preview() // fresh canonical fixture root
  assert.equal(preview.enabled, true)
  assert.equal(preview.change, "create")
  await assert.rejects(stat(join(root, ".vibe-workflow")))
  ```

- [ ] Write `test_repeat_preserves_off_and_unknown_fields`: inspect/preview preserve false; applying true changes only the owned preference and retains invented unknown fields.
- [ ] Write tests for nested directories, separate nested repositories and linked worktrees; assert the exact target root, with primary/parent settings bytes unchanged.
- [ ] Write tests for symlink/hardlink/unsafe modes, malformed/oversized JSON, unknown version, nonboolean `enabled`, stale snapshot, held lock and failed replacement. Refusal must preserve existing bytes and echo no synthetic private sentinel.
- [ ] Observe RED: `node --test tests/project.test.js`.
  Expected: tests fail on the missing project-settings interface.
- [ ] Implement the interfaces using Node built-ins. Cap JSON at 64 KiB and Git discovery at 2 seconds; no recursive project scan. Missing file is distinct from an unreadable file. New directory/file modes are `0755`/`0644`; preserve an existing safe file's mode.
- [ ] Observe GREEN: `node --test tests/project.test.js`.
  Expected: all tests pass with no real-home writes or unexpected Git hooks.
- [ ] Commit this task, then rerun the same command on the committed content. Record results and residual local-writer race limits.

## Task 2: Preview/Apply Chat Onboarding

**Files:** Create `onboarding.js`, the two skill files and `tests/onboarding.test.js`; modify `index.js`, `runtime/selector.py`, `scripts/select.py`, package metadata/manifest/validation, package fixtures and guidance.

**Interfaces:**
- Consumes Task 1's project-settings object.
- `registerOnboarding(ctx, project, checkSetup)` registers only `vibe_workflow_onboard` and `vibe-workflow-onboard`.
- `checkSetup(signal)` returns `{state: "ready" | "setup_needed", reason: string | null}`.
- `runtime.selector.check_setup(*, state_dir: Path) -> dict` supplies that pure local result through `scripts/select.py --state-dir ABSOLUTE --check-setup`.
- Existing `runtime.selector.select(request, *, state_dir, source=None)` remains compatible.
- Private `runRouter(request, {directory, stateDir, signal, operation})` in `index.js` shares the existing child lifecycle between `operation: "select" | "check_setup"`. Validate the response against that operation's fixed shape; setup readiness is never a selected route.

- [ ] Write onboarding tests showing first preview defaults ON, repeat preview preserves OFF, and cancelled or merely previewed choices create no files.
- [ ] Write `test_permission_denial_never_applies`: the native permission assertion rejects; the original settings and unrelated permission/tool objects remain unchanged.

  ```javascript
  await assert.rejects(onboard.execute({
    v: 1, op: "apply", enabled: true, expected_snapshot: preview.snapshot,
  }, invocation)) // fixture host denies edit; never replace apply itself with a fake
  assert.equal(await readFile(settingsPath, "utf8"), originalBytes)
  assert.equal(tools.get("subagent"), originalSubagent)
  ```

- [ ] Write apply tests asserting real snapshot-conditioned persistence, exact target path, conflict refusal and no unknown stored values in the tool's preview. A lost acknowledgement requires fresh inspection, not an automatic second apply.
- [ ] Write Python setup tests: missing/disabled/empty/invalid/unsafe router configuration returns `setup_needed`; valid supported routes return `ready`; no native reader, account lookup or state write occurs.
- [ ] Write an exported-package test that imports the actual plugin and finds both tool registrations and the bundled onboarding skill/procedure without access to the repository.
- [ ] Observe RED:
  `node --test tests/onboarding.test.js`
  and `python3 -B -m unittest tests.test_selector tests.test_package`.
  Expected: new registration/setup assertions fail before implementation.
- [ ] Implement registration with the documented OpenCode `ctx.tool.transform`, `ctx.skill.transform` and `ctx.permission.assert` APIs. Load bundled skill text before synchronous transforms. Keep the skill entry short and the full procedure in its reference; use the installed instruction-authoring skills during execution.
- [ ] Reuse the existing bounded child lifecycle for setup inspection instead of adding another process runner. Preserve cancellation, timeout, output caps and fixed errors. Refactor common configuration loading only where needed to prevent setup/selection validation drift.
- [ ] Write the approved question copy verbatim from the spec. Procedure order: read-only preview, display context, native question, refresh preview if the answer changes, show proposed edits, wait for confirmation, apply, report readiness. Future capabilities add no questions.
- [ ] Add only `project.js`, `onboarding.js`, `skills/onboard-project/SKILL.md` and `skills/onboard-project/references/procedure.md` to the runtime allowlist: 26 production files total. Keep `PRODUCT.md`, design/plan, tests, project settings and evidence excluded.
- [ ] Update fake contexts with skill registration and exact permission behavior. Fixtures must copy the new local import/skill dependencies explicitly, not the checkout or ignored files.
- [ ] Observe GREEN:
  `node --test tests/project.test.js tests/onboarding.test.js tests/plugin.test.js`
  and `python3 -B -m unittest tests.test_selector tests.test_package`.
  Expected: all tests pass; configuration inspection makes zero acquisition calls.
- [ ] Commit, then repeat those tests and `python3 -B scripts/validate.py`. Record package proof separately from live client behavior.

## Task 3: Project-Aware Advisory Use and Non-Blocking Fallback

**Files:** Modify `index.js`, plugin/package tests, the two opt-in verification fixtures and user guidance. Add no new runtime module.

**Interfaces:**
- Consumes Task 1 inspection plus Task 2 registrations and setup-check transport.
- The selector tool retains its existing request and authority fields; it may add `notice: string | null` to its result.
- Missing/OFF/invalid project settings return `state: "no_selection"`, `route: null` and reasons `project_not_onboarded`, `project_disabled` or `project_settings_invalid` before any reader process.
- Enabled failures retain the selector's fixed reason/exclusions; the native caller receives a notice, not an alternative route.

- [ ] Write tests proving missing, OFF and malformed project settings spawn no Python/native reader even on an explicit selector call. Onboarding remains discoverable and other tools/context entries remain intact.

  ```javascript
  const result = JSON.parse((await selectTool.execute({v: 1, work_kind: "review"}, invocation)).content)
  assert.equal(result.reason, "project_disabled") // fixture settings explicitly opt out
  assert.equal(result.route, null)
  assert.equal(childStarts, 0)
  ```

- [ ] Write an ON-project test against the existing synthetic selector transport, asserting exact provider/model/effort delivery and unchanged false authority fields. Add a real `.vibe-workflow/settings.json` fixture so the preference gate cannot make a reader-failure test pass without exercising its reader.
- [ ] Write fallback tests for unconfigured/empty table, unverified billing, unknown overflow, positive quota exhaustion, changed account and transport failure. Each enabled failure supplies a short `vibe-workflow` notice with a matching action and no raw private data.
- [ ] Pin the generic copy:

  ```javascript
  assert.equal(result.notice,
    "vibe-workflow: Smart Router found no eligible route. Check routing settings or account availability. Continuing with your client's existing selection.")
  assert.equal(result.route, null)
  assert.equal(nativeDispatchCalls.length, 0)
  ```

- [ ] Test once-per-cause notices per session, isolation between sessions, clearing after success/opt-out, and no notice on intentional OFF. Bound the map to 128 recent sessions; cleanup discards it.
- [ ] Observe RED: `node --test tests/plugin.test.js`.
  Expected: project gating, fallback or caller-instruction assertions fail.
- [ ] Implement gating before the existing child spawn. Context adds automatic-consultation instructions only for valid ON projects; it never invokes the router while assembling context. No Python spawn per context hook, background file watch or native tool rewrite.
- [ ] Map known reasons to corrective actions: configuration/table problems → check setup/table; unverified billing → check native sign-in/account status; unblocked/unknown overflow → review paid-overflow settings; positive exhaustion → check limits or try later; account change → check selected account. Use the generic notice when no specific cause is proved. Add no automatic remediation or retry.
- [ ] Keep native fallback instruction-driven: caller shows `notice`, then retains its existing selection if no recommendation exists. Do not catch an onboarding edit denial as a routing failure, or interpret fallback as permission to bypass a native denial.
- [ ] Preserve the existing cancellation verifier's actual exported plugin/CLI/synthetic-reader path. Its fake host must select the `smart_router_select` tool by name, support skill registration and create an ON project preference; otherwise it would test the opt-in gate instead of acquisition cancellation.
- [ ] Extend native loading observation to require the active plugin, both tool registrations, the onboarding skill's actual bundled source and zero model/session activity. Run no onboarding apply against a real checkout.
- [ ] Observe GREEN on the committed candidate:

  ```sh
  python3 -B -m unittest discover -s tests
  node --test tests/*.test.js
  python3 -B scripts/validate.py
  python3 -B scripts/package.py --output NEW_PRIVATE_PACKAGE_DIRECTORY
  python3 -B scripts/validate.py --root NEW_PRIVATE_PACKAGE_DIRECTORY
  ```

  Expected: full suites pass; the exact 26-file export validates. The output directory must be new; never overwrite an existing export or rebuild beneath an unexamined `dist/`.
- [ ] Run one credential-free, zero-prompt native loading check and the existing synthetic mid-acquisition cancellation check against that export, in fresh private directories. Report native registration, caller behavior, reader behavior and unavailable live-agent compliance separately. No real-provider/model trial.
- [ ] Obtain one independent behavior/security review of the committed change and export. Cover root ownership, safe writes, preview/permission boundaries, opt-out acquisition suppression, fallback semantics and coexistence. Resolve blocking findings with focused RED→GREEN checks within the review's original budget.
- [ ] Deliver local source and a validated `dist/` package only. If refreshing an existing owned export, verify and retain its exact previous contents outside `dist/` before building into the now-absent path. Leave unrelated files, primary source branch, remote refs and global/project installations unchanged.

## Plan Self-Review and Handoff

- The approved UX and project path map to Task 1 persistence, Task 2 conversation and Task 3 advisory integration.
- Every Review Focus case has a named test step. Task interfaces agree on preview snapshots, setup states and tool names.
- The new-project ON default never changes the shipped global disabled examples or silently enrolls existing projects.
- Native registration/fixtures prove integration wiring, not that every model will obey the caller instructions. Live-agent compliance remains an explicit qualification limit.
- Self-review found no unmapped spec requirement. Git root discovery, settings ownership/modes, snapshot-conditioned apply, setup inspection, native registration and notice behavior are concrete plan choices, not additional user-selected features.
- No onboarding implementation, project-settings file, package refresh, install, activation or inference occurs while preparing this plan.
- Execution method has not been selected for this onboarding plan. Recommend **Native** inline execution plus one final independent review: three small tasks share settings and registration interfaces.
