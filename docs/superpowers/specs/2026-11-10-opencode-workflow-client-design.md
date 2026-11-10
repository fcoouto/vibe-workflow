# OpenCode as a full workflow client: design

Date: 2026-11-10. Status: **design for coordinator review.** Implementation slices start only after the coordinator accepts it. This is slice R4 of the [workspace readiness plan](../plans/2026-11-09-workspace-readiness-and-onboarding.md#7-opencode-as-a-full-plugin-client-decided-2026-11-10). It answers the open questions of the earlier brief (draft PR #190, `docs/superpowers/specs/2026-11-10-opencode-full-client-brief.md`, never merged) and supersedes it.

## Goal and boundaries

The user asked to "qualify a real workflow client" on OpenCode (2026-11-10). OpenCode sessions must get what Codex and Claude Code sessions get: orientation at startup and after context loss in enrolled checkouts only, the packaged skills, enrollment through onboarding, readiness and version checks, and local worker-selection guidance that preserves the coordinator's model.

Parity target: the **Claude Code level** (startup orientation, skills, enrollment, readiness). Codex's optional task runtime (`PreToolUse`/`PostToolUse`/`Stop`, `runtime/state.py` task controls, the runtime-backed guidance cache) stays Codex-only; Claude Code does not have it either. Smart Routing's OpenCode shim, launcher and accounting are unchanged.

Binding constraints: [AGENTS.md](../../../AGENTS.md#repository-constraints) (adapter layout, preserve customizations, prefer native capabilities, 1000-line Python files with fixed legacy ceilings) and the user's lightweight rule (`~/ai-memory-vault/smart-router-lightweight.md`): on-demand work only, no timers, daemons or polling, no extra model calls, short bounded processes, cheap hot paths, and every added cost stated with its lighter alternative.

## Current state against Codex and Claude Code

| Capability | Codex | Claude Code | OpenCode today |
| --- | --- | --- | --- |
| Startup registration | `SessionStart` in [hooks/codex.json](../../../hooks/codex.json) (lines 3–14), registered by [.codex-plugin/plugin.json](../../../.codex-plugin/plugin.json) line 6 | `SessionStart` in [hooks/claude-code.json](../../../hooks/claude-code.json) (lines 3–14), registered by [.claude-plugin/plugin.json](../../../.claude-plugin/plugin.json) line 6 | **None.** [clients/opencode.md](../../../clients/opencode.md) line 3: "not an onboarding or startup client" |
| Re-orientation after compaction | Same hook, source `compact` | Same hook, source `compact` ([runtime/adapters/claude_code.py](../../../runtime/adapters/claude_code.py) line 7) | **None.** Only `AGENTS.md` and `instructions` load natively; nothing is enrollment-aware |
| Hook dispatch | `scripts/workflow.py` line 374 (`adapters = {"codex": …, "claude-code": …}`) | same | Rejected: "Expected a supported client and event" (line 375–376) |
| Event normalizing | [runtime/adapters/codex.py](../../../runtime/adapters/codex.py) | [session_start.py](../../../runtime/adapters/session_start.py) lines 8–20 | [runtime/adapters/opencode.py](../../../runtime/adapters/opencode.py) covers routing hooks only |
| Orientation text | [runtime/startup.py](../../../runtime/startup.py) lines 445–458 (Codex reader variant) | lines 437–444 (generic) | Generic text would apply unchanged; it points at `clients/<client>.md` (line 435), which for OpenCode is the 82 KB routing contract |
| Enrollment | Accepted in onboarding sources ([runtime/onboarding.py](../../../runtime/onboarding.py) line 280) | same | Refused at line 280; [managed-plan.md](../../../skills/onboard-my-project/references/managed-plan.md) line 25 and the [onboarding procedure](../../../skills/onboard-my-project/references/procedure.md) line 11 call it an explicit gap |
| Startup membership read | `runtime/startup.py` line 392 accepts any listed client | same | Already client-generic; no change needed |
| Delivery note (hook ran) | [runtime/hook_delivery.py](../../../runtime/hook_delivery.py) line 18, `workflow.py` line 440 | same | Absent from `HOOK_FILES` and line 440 |
| Installed version | [runtime/package_versions.py](../../../runtime/package_versions.py) lines 17–18 (`codex plugin list`) | same (`claude plugin list`) | Absent |
| Readiness cells | [workspace-requirements.json](../../../clients/workspace-requirements.json) `package`, `hooks-trust` | same | Lines 41 and 54: `not-supported` ("pending (plan slice R4)") |
| Skills | `"skills": "./skills/"` in `.codex-plugin/plugin.json` line 5 | default `skills/` dir ([clients/claude-code.md](../../../clients/claude-code.md) line 9) | **None.** No `skills` config entry, no plugin transform |
| Worker selection guidance | Runtime-backed cache, surfaces `codex-cli`/`codex-desktop` only (`scripts/workflow.py` line 229; requires runtime state, lines 257–260) | **None** | **None.** Native `subagent` tool exists; [opencode.py](../../../runtime/adapters/opencode.py) lines 256–267 already parse a per-call `model: provider/model#variant` |
| Managed delegation | Advisory, via `prepare` | none | Managed launcher; [scripts/routing.py](../../../scripts/routing.py) lines 96–102 already list `opencode` as forward-compatible |
| Titles | runtime title sync | — | Ordinary-path [title command](../../../clients/workstreams-titles.md#opencode-sessions) |

### The gap list, corrected

- **(a) Startup and compaction hook: confirmed.**
- **(b) Client acceptance: partly wrong.** Needed: `runtime/onboarding.py` line 280, `scripts/workflow.py` lines 374 and 440, `runtime/hook_delivery.py` line 18, `runtime/package_versions.py` lines 17–18, `runtime/workspace.py` (`check_hooks` line 144 picks a Codex or Claude parser), `clients/workspace-requirements.json`, `clients/startup.md` (`clients` field) and the onboarding skill text. **Not needed:** `runtime/state.py` (lines 1443, 1855, 2096) and `runtime/observability.py` (line 57) gate Codex task-runtime records; Claude Code is only partly admitted there. `runtime/startup.py` is already client-generic.
- **(c) Skill packaging: confirmed.**
- **(d) Worker-selection guidance: confirmed, and Claude Code lacks it too.** The existing guidance cache needs Codex runtime state, so it cannot be reused for a startup-only client.
- **(e) Missing from the list: the client contract file.** Startup and onboarding require `clients/<client>.md` (`runtime/startup.py` lines 405 and 435, `runtime/onboarding.py` line 281). For OpenCode that file is the Smart Routing contract (350 lines, 82 KB). Pointing every session at it would cost about 20,000 tokens and load unrelated rules.

## Design

### A. Orientation plugin (gap a)

**Mechanism.** One new OpenCode V2 server plugin, `runtime/adapters/opencode_workflow.ts`, id `vibe-workflow.workflow`. It has no dependencies, holds no policy and stays separate from the routing shim (which must stay policy-free and is loaded by managed launches). It uses only these documented V2 APIs (OpenCode docs v2.1.2 through Context7: `build/plugins/index.mdx`, `migrate-v1.mdx`):

- `ctx.session.hook("context", …)`: runs right before each agent-loop model request, never for `title`, `generate` or `compaction` requests. The handler appends the cached orientation to `event.system`.
- `ctx.session.hook("compaction", …)`: marks that session for re-orientation. It never sets `result` and never edits the compaction prompt.
- `ctx.session.get(id)`: read once per session to learn the parent and directory.

It does not subscribe to the event stream, uses no timer and never logs.

**Flow, per session and plugin instance:**

1. The first `context` call for a session decides eligibility once. A session is skipped when it is a child (it has a parent): this matches the native-only Workstreams rule that only coordinator (root) sessions are oriented ([design](2026-11-10-workstreams-native-only-design.md)). It is also skipped when its ID has the ledger-derived shape (`ses_` plus 32 lowercase hex), that is a Smart Routing managed worker, whose brief already carries its instructions and whose tokens are accounted. A failed `session.get` counts as a root, because orientation is guidance only.
2. For an eligible session, the plugin runs `python3 -I -B <package>/scripts/workflow.py opencode SessionStart` once. Stdin gets `{hook_event_name:"SessionStart", session_id, cwd, source}`, where `cwd` is the session's directory and `source` is `startup`, or `compact` after a compaction. The package root comes from `import.meta.url`, as in the routing shim's `defaultArgv`. A `python` plugin option overrides the interpreter: desktop apps often get a minimal `PATH` whose `python3` is the macOS 3.9 stub.
3. The plugin caches the result per session: the `additionalContext` text, a diagnostic, or "nothing" (unenrolled). It keeps at most 256 sessions and evicts the oldest. Concurrent first calls share one in-flight promise.
4. Every later `context` call appends the cached text when there is one. The text is byte-stable, so provider prompt caching applies.
5. A `compaction` call clears that session's cache entry and sets `source` to `compact`, so the next `context` call reruns step 2.

**Python side.** The new module `runtime/adapters/opencode_startup.py` follows the `claude_code.py` pattern (about 15 lines). It sets `SOURCES = {"startup", "compact"}`, reuses `session_start.normalize`, `render` and `diagnostic`, and adds one fixed sentence for `compact`: "Context was compacted: restore missing instructions and check actual project state before continuing" (policy 07, handoff and resumption). `scripts/workflow.py` registers `"opencode"` in `adapters` and in the delivery-note set. `runtime/startup.py` is not changed: it is at its 1515-line ceiling, and its generic orientation already fits.

**What restores state after context loss.** System text is not part of the transcript, so compaction never removes it. The orientation is present on every agent request before and after compaction. The rerun after compaction refreshes it (enrollment and delivery note) and adds the restore sentence. That matches what Codex and Claude Code get from `SessionStart` `compact`. No task state is injected: startup-only scopes have none, as on Claude Code.

**Resume.** OpenCode has no resume event. A restarted server is a new plugin instance with an empty cache, so the first request of a resumed session reruns step 2 with `startup`. That is equivalent, because `orient` does not depend on the source.

### B. Enrollment (gap b)

- `runtime/onboarding.py` line 280 accepts `"opencode"`. The edit replaces a tuple and adds no lines; the file is at its legacy ceiling.
- `clients/startup.md` lists `opencode` among the `clients` values. [managed-plan.md](../../../skills/onboard-my-project/references/managed-plan.md) and the [onboarding procedure](../../../skills/onboard-my-project/references/procedure.md) step 1 lose the "until its plugin client ships" gap.
- Canonical startup-only membership (`configured`, `runtime:null`) is written by the existing file-configuration path, as for Claude Code ([policy 10](../../../policies/10-project-onboarding.md#client-startup-integration)).
- [Policy 03](../../../policies/03-agent-delegation.md#managed-projects) "Onboarding records only Codex and Claude Code today…" and the `scripts/routing.py` comment become stale and are updated in the contracts slice.

### C. Skills (gap c)

**Mechanism (native, no code).** Add the package's `skills/` directory to the `skills` array of the user's global OpenCode config. OpenCode V2 combines `skills` arrays from every config file, and absolute paths are used as written (`skills.mdx`). The native `skill` tool then lists and loads the nine packaged skills by their frontmatter `name`.

Lighter-or-equal alternative: the plugin registers them with `ctx.skill.transform`. That needs no second config entry, but it reads nine files at every plugin load and adds code. **Recommendation: the config entry.**

Unobserved, so the live qualification must check them: whether relative links inside a loaded `SKILL.md` resolve, because the skill tool reports the path; and what happens when another source defines the same skill name.

### D. Worker selection (gap d)

The local guidance cache in policy 03 needs Codex runtime state (`scripts/workflow.py` lines 257–260), which a startup-only client does not have. The lightweight equivalent is a **packaged guidance section** in the new short `clients/opencode.md`. It names the OpenCode dispatch controls, the official source URLs, the checked date, the OpenCode version applicability and a SHA-256 digest per source. Workflow maintenance (policy 09) refreshes it on the policy's triggers, never in a hook. It holds:

- **Dispatch control:** the `subagent` tool (`agent`, `description`, `prompt`, optional `background`, `sessionID` to continue). The per-call `model: "provider/model#variant"` is not in the 2.1.2 docs, but the adapter already parses it (`runtime/adapters/opencode.py` lines 256–267) and the capability matrix lists it among checkable inputs. Its effect on 2.2.6 must be observed live before guidance relies on it.
- **Preserve the coordinator:** never write the top-level `model` or `small_model`, a primary agent (`build`, `plan`) or a user-defined agent. Choose worker settings per call. When only inheritance is available (a subagent runs on its own agent's `model`, else the parent's), record that limitation, as policy 03 requires.
- **Record** requested settings (the spawn input) separately from observed ones (the child's stored assistant-message `model`, which the [title and accounting readers](../../../runtime/opencode_titles.py) already reach through `opencode api`).
- **Managed projects:** unchanged. OpenCode workers go through the [managed launcher](../../../clients/opencode.md#managed-launcher), and a native `subagent` in a managed project is not a routed dispatch.

Which providers' official model guidance this section covers is a user decision (below).

### E. Readiness, version and delivery

- **Delivery note:** `HOOK_FILES["opencode"] = "runtime/adapters/opencode_workflow.ts"`, so the note binds the plugin file's hash, as `hooks/*.json` does for the others. `workspace status` reports `vibe-workflow-hooks` `ready` for OpenCode only from a fresh note. There is no trust approval to observe: OpenCode loads configured plugins without one.
- **Installed version:** `package_versions.observe("opencode")` reads the global `opencode.json`/`.jsonc` with the existing `workspace_observe.opencode_config_plugins` and finds the entry whose path is a `vibe-workflow` package's plugin file. It then reads that package's version and checks that the `skills` entry names the same package. This needs **no subprocess**, which is cheaper than the Codex and Claude CLI listings.
- `clients/workspace-requirements.json`: the OpenCode cells of `vibe-workflow-plugin` and `vibe-workflow-hooks` become `package` and `hooks-trust`.
- Startup drift and readiness warnings come for free through `startup_notes.apply` once `workflow.py` dispatches `opencode`.

### F. Client contract file (gap e)

**Recommendation:** rename the routing contract to `clients/opencode-routing.md` and make `clients/opencode.md` a short client contract (startup behavior, installation, skills, worker guidance), like `clients/claude-code.md`. Then `clients/<client>.md` keeps one meaning and `runtime/startup.py` stays unchanged. The rename touches 68 references in 21 files. `docs/smart-routing-qualification.md` and `tests/qualification/opencode_live.py` belong to open PR #241, so the rename waits until #241 merges.

Alternative: a client-file mapping in `runtime/startup.py` and `runtime/onboarding.py`. That avoids the rename, but adds a rule and lines to two files at their ceilings.

## Installation: preparation and onboarding stay distinct

| Stage | Owner | What it does for OpenCode |
| --- | --- | --- |
| Environment preparation (machine/user scope) | `prepare-my-workspace` step 4 | Backs up the global config file in the run directory (step 3). Adds two entries to it: the plugin (absolute path to the package's `runtime/adapters/opencode_workflow.ts`, with `options.python` when the default interpreter is too old) and the package's `skills/` directory. Step 6 needs a new OpenCode session. Step 7 records them. |
| Project onboarding | `onboard-my-project` | Step 0 runs `workspace status`. Missing OpenCode plugin or hook cells are announced and handed to preparation, then onboarding resumes; it never edits client config itself. Step 5 writes startup-only canonical membership for `opencode`. Step 6 verifies orientation in a new OpenCode session. It writes no project `opencode.json`. |

The package path is the clean export (`~/.cache/vibe-workflow/dev-package` on this machine, [setup record](../../setup.md#client-integration)) or a release copy, never a client cache. An upgrade refreshes that directory, so both entries follow it in one step.

## Preserving the user's OpenCode configuration

Edit-in-place rules for the global `opencode.json` or `opencode.jsonc`:

- Prefer the native CLI (`opencode plugin add <path>`) when 2.2.6 accepts a local path; otherwise make a minimal textual insertion that keeps comments, order and formatting. Never re-serialize the whole file.
- Append only. Never reorder, rewrite or remove another `plugins`, `plugin` (V1 key), `skills`, `instructions`, `mcp`, `agents`, `permissions`, `model` or `small_model` entry. If the file uses the V1 `plugin` key and 2.2.6 does not read both keys, stop and leave the cell `needs-user` with the exact snippet.
- Skip an entry that already names the same package (idempotent). A different `vibe-workflow` copy is `outdated`; the upgrade procedure replaces only that entry.
- Undo: restore the backup only while the file still matches the post-edit digest; otherwise remove exactly the two inserted entries and report it. Record both paths, digests and the undo step in the [setup record](../../setup.md#maintenance-and-undo).
- Never touch credentials (`auth.json`), the desktop app's state or project config files.

## Failure modes

| Failure | Behavior | Kind |
| --- | --- | --- |
| Checkout not enrolled for `opencode` | Python prints nothing; the plugin caches "nothing" and injects nothing | Silent, by design |
| `python3` missing or older than 3.11 | `workflow.py` returns its version diagnostic; the plugin injects it as system text for that session | Degrade: session works, workflow readiness not claimed |
| Spawn error, non-zero exit, malformed or oversized output (over 64 KiB) | Fixed diagnostic text ("Vibe Workflow startup unavailable: …"), cached for the session | Degrade |
| Timeout (5 s hard; process group SIGTERM, then SIGKILL) | Same diagnostic; no retry until compaction or a new plugin instance | Degrade, no loop |
| Invalid enrollment or policy digest drift | Python's existing diagnostic, as on Codex and Claude Code | Degrade, reported |
| Plugin fails to load | No orientation and no delivery note, so `workspace status` stays not ready. OpenCode's load-failure behavior is unobserved (capability matrix P0.6) | Visible through readiness |
| `session.get` fails | Session treated as root and oriented | Degrade toward guidance |
| Derived-ID session (managed worker) | Skipped; no spawn | By design |
| Skills directory moved or missing | Skills unavailable; readiness reports the package cell `outdated` or `missing` | Visible through readiness |
| Config edit unsafe (V1 key, unparseable JSONC) | No edit; `needs-user` with the snippet | Fail closed for the edit |

Orientation never blocks a session or a tool. The workflow's claims fail closed: without an observed orientation, readiness is not claimed.

## Cost under the lightweight rule

| Piece | Added cost | Lighter alternative |
| --- | --- | --- |
| Plugin load | No process, no event subscription, small in-memory map | — |
| Orientation, first request of each root session | One short Python process (bounded 5 s; typical startup plus Git lookup well under 1 s), on the critical path of that one request | A native `instructions` entry: zero processes, but no enrollment check (it shows in unenrolled repositories or needs a project file), no diagnostics, delivery note or drift warning |
| Orientation, every later request | About 200 tokens of byte-stable system text per agent request (cache-friendly). Codex and Claude Code inject it once into the transcript instead | Inject only on the first request with the `prompt` hook. Compaction would then drop it, so the restore guarantee is lost |
| Compaction | One more Python process per compaction | Skip the rerun and keep the cached text, losing only the refresh and the restore sentence |
| Child and managed-worker sessions | One in-process `session.get` per session; no process; no tokens | — |
| Skills | None at runtime beyond OpenCode's own discovery | — |
| Readiness and version | Two small config-file reads, no subprocess | — |
| Delivery note | At most one small write per 24 h, inside the hook deadline | — |
| Model calls | **None** | — |

## Shared-core touch points (full suite)

`scripts/workflow.py`, `runtime/onboarding.py`, `runtime/hook_delivery.py`, `runtime/package_versions.py`, `runtime/workspace.py`, `clients/workspace-requirements.json`, `scripts/validate.py` (a package check for the plugin file), `policies/03-agent-delegation.md` and the instruction map.

A policy edit changes the legacy schema-1 `policy_digest`, so legacy-enrolled checkouts get the "Installed policies differ" diagnostic until they are re-accepted. All policy edits therefore go into one slice. Each slice that touches these files runs the full `scripts/test_sharded.py` once on its committed head, as the development guide requires.

## Slice plan

Each slice writes its tests first, has non-overlapping owned files and can merge on its own. Whichever slice merges later rebases and regenerates `clients/instruction-map.json`.

| Slice | Owns | Test first | Depends on | Suite |
| --- | --- | --- | --- | --- |
| **S1 Python client acceptance** | `runtime/adapters/opencode_startup.py` (new), `scripts/workflow.py` (adapter and note set), `runtime/onboarding.py` line 280, `clients/startup.md`, `skills/onboard-my-project/references/managed-plan.md`, `tests/test_opencode_startup.py` (new) | Payload for `opencode SessionStart` produces orientation in an enrolled fixture, nothing when unenrolled, a diagnostic for a bad payload, the restore sentence for `compact`; onboarding source record accepts `opencode` | — | Full |
| **S2 Orientation plugin** | `runtime/adapters/opencode_workflow.ts` (new), `tests/opencode_workflow/*.test.ts` (new, `node --test` with a fake `workflow.py`), `runtime/hook_delivery.py`, `scripts/validate.py`, `tests/test_hook_delivery.py` | Context hook injects once-spawned text; child, derived-ID and failed-spawn cases; compaction reruns with `compact`; timeout kills the group; no stdout logging | S1 | Full |
| **S3 Readiness and version** | `runtime/package_versions.py`, `runtime/workspace.py`, `runtime/workspace_observe.py`, `clients/workspace-requirements.json` and `.md`, `tests/test_package_versions.py`, `tests/test_workspace.py` | Config-listed plugin and skills entries give `installed` with the version; a mismatched package gives `outdated`; a fresh note gives hooks `ready`; nothing spawns | S2 | Full |
| **S4a Routing contract rename** | `clients/opencode.md` renamed to `clients/opencode-routing.md`, plus every inbound reference (the 21 files found by `grep -rl opencode.md`) | Validate, the link and anchor checks and the tests that pin anchors | #241 merged | Full (it touches `policies/03` and code comments) |
| **S4b Client contract, skills and guidance** | New short `clients/opencode.md`, `clients/index.md`, `policies/03-agent-delegation.md` (one policy-digest change), `docs/onboard-my-project-plan.md` (client scope), `skills/onboard-my-project/references/procedure.md`, `skills/prepare-my-workspace/references/procedure.md` (OpenCode install, backup and undo), `docs/policy-sources.md`, `scripts/routing.py` comment, `clients/instruction-map.json` | Representative-task instruction checks: an OpenCode startup loads the short contract and the index, not the routing contract; a preparation run loads the OpenCode install steps; delegation loads the worker guidance | S1–S3, S4a | Full |
| **S5 Live qualification record** | `docs/opencode-client-validation.md` (new), `docs/setup.md` (client integration), `tests/opencode_client_live.py` (new; skipped unless explicitly enabled) | — (observation) | S1–S4b | Static checks |

S5 avoids `tests/qualification/*`, which belongs to #241.

## Live qualification procedure

The coordinator runs this at the end from its own OpenCode 2.2.6 Desktop session, through its shell tool. Part A uses **zero real model calls** and touches no real user state. Part B makes **one** short model call and needs the user's approval, because it edits the real global config.

### Part A: disposable server with a local stub provider (zero model calls)

1. Make a private temp root and set `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME` and `XDG_CACHE_HOME` under it, plus `OPENCODE_DISABLE_AUTOUPDATE=1` and `OPENCODE_DISABLE_MODELS_FETCH=1`. Assert that every one of these paths is inside the temp root before starting anything. Never pass the real `~/.config/opencode`.
2. Write a temporary config there containing only the workflow plugin and `skills` entries for the export under test, plus one OpenAI-compatible provider whose base URL is a loopback stub. The stub records request bodies and returns canned replies. Make two disposable Git fixtures: one enrolled for `opencode` through the S1 onboarding path, one unrelated.
3. Start `opencode serve` on loopback in the enrolled fixture, create a session through the HTTP API and send "ping". **Proves the hook loaded:** the stub's recorded request has the orientation sentence ("Vibe Workflow applies to this enrolled checkout…") in its system text, and `$XDG_STATE_HOME/vibe-workflow/delivered/opencode.json` names the package version and plugin hash. Repeat in the unrelated fixture: no orientation.
4. **Skills resolve:** the stub returns a canned `skill` tool call for `onboard-my-project`. The tool result holds that `SKILL.md`, and a relative link in it resolves. The server's skill listing names all nine packaged skills.
5. **Worker selection follows guidance:** the stub returns a canned `subagent` call with `model: "stub/worker"` while the session uses `stub/coordinator`. The stub sees the child's request on `worker` with **no** orientation text (child skipped), and the parent's next request still on `coordinator`. The config file is byte-identical afterwards.
6. **Compaction restores state:** trigger compaction through the session API. The stub gets the compaction request with the prompt unchanged by the plugin. The next agent request again carries the orientation plus the restore sentence.
7. Undo: stop the server's process group and delete the temp root. No real file changed.

Prerequisite and limit: OpenCode must be able to load an OpenAI-compatible provider offline. If 2.2.6 needs to fetch the provider package, Part A is limited to steps 1–3 with a disposable route, and steps 4–6 move to Part B.

### Part B: real Desktop session (one model call, approval required)

1. With the user's approval, run `prepare-my-workspace` for the OpenCode cells. It backs up the global config and adds the two entries. Then onboard a disposable checkout for `opencode`.
2. Open a **new** Desktop session in that checkout and send one short prompt: "Name the file the Vibe Workflow startup context told you to read first." That is one call on the coordinator's own model. The answer names the policy index or client file, the delivery note is fresh, and `python3 -B scripts/workflow.py workspace status --client opencode` shows the plugin and hooks cells `ready`. The session's skill list shows the packaged skills.
3. Undo: the preparation run's rollback restores the config backup (or removes exactly the two entries), and the disposable checkout's onboarding rollback removes its membership.

Report observed client execution separately from package and fixture checks.

## Open decisions for the user

1. **Orientation mechanism:** the plugin `context` hook (recommended: parity with enrollment gating, one process per root session, about 200 cached tokens per request), or the native `instructions` entry (zero processes, no enrollment gate, no diagnostics).
2. **Child sessions:** orient root (coordinator) sessions only, as the native-only Workstreams design implies; confirm.
3. **Distribution:** a local path to the clean export or release copy (recommended; no network), or `opencode plugin add github:fcoouto/vibe-workflow…`, which needs a `package.json` and network at install.
4. **Skills:** a global `skills` config entry (recommended) or a plugin `skill.transform`.
5. **Worker guidance coverage:** which providers' official model guidance OpenCode's local copy carries (Claude Agent provider, ChatGPT sign-in route, OpenCode Go/Zen, OpenRouter).
6. **If 2.2.6 ignores the per-call `subagent` `model`:** may preparation add dedicated worker agents with their own `model` to the user's config, or is inheritance with a recorded limitation acceptable?
7. **Compaction prompt:** leave it untouched (recommended), or add a short "keep task goal, plan step, branch and pending checks" line (about 40 tokens per compaction).
8. **Managed launches:** should managed OpenCode launches ever load the workflow plugin? The plugin skips derived-ID sessions either way, but the launcher's pre-canary requirement wants no extra plugin. Smart Routing owns that answer.
9. **Part B approval:** editing this machine's real OpenCode config for the live check.
10. **Runtime task controls on OpenCode** (Codex-style `PreToolUse`/`Stop` and task state): out of scope here; confirm the Claude Code-level target.

The coordinator, not the user, decides the contract layout: the S4a rename (recommended) or a client-file mapping. Draft PR #190 can then be marked superseded.

## Not yet observed

Everything about 2.2.6 in this design comes from the 2.1.2 documentation or the repository's recorded probes, not from a 2.2.6 run of this plugin. The live qualification must observe: that `context` system additions reach the request and are not duplicated; that the hook does not fire for `title` and `compaction` requests; that the `compaction` hook fires before summarizing; child-session parent data from `session.get`; the per-call `subagent` `model`; `skills` array support and relative links; V1 `plugin` versus V2 `plugins` key handling; and the desktop app's `PATH` for `python3`.
