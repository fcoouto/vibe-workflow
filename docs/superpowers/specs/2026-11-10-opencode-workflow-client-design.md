# OpenCode as a full workflow client: design

Date: 2026-11-10. Status: **design, revised after independent review; scope decision pending.** Implementation slices start only after the coordinator accepts it and the user settles the scope (decision 10). This is slice R4 of the [workspace readiness plan](../plans/2026-11-09-workspace-readiness-and-onboarding.md#7-opencode-as-a-full-plugin-client-decided-2026-11-10). It answers the open questions of the earlier brief (draft PR #190, `docs/superpowers/specs/2026-11-10-opencode-full-client-brief.md`, never merged) and supersedes it.

## Goal and boundaries

The user asked to "qualify a real workflow client" on OpenCode (2026-11-10). OpenCode main sessions must get what Codex and Claude Code sessions get: orientation at startup and after context loss in enrolled checkouts only, the packaged skills, enrollment through onboarding, readiness and version checks, and local worker-selection guidance that preserves the coordinator's model.

Proposed parity target: the **Claude Code level** (startup orientation, skills, enrollment, readiness). The user has not decided the scope yet; the [feature × adapter comparison](#feature--adapter-comparison) is what that decision needs. Codex's optional task runtime stays Codex-only in this proposal: `PreToolUse`/`PostToolUse`/`Stop`, the `runtime/state.py` task controls and the runtime-backed guidance cache. Smart Routing's OpenCode shim, launcher and accounting keep their behavior; the only launcher change is one marker variable (part G).

Binding constraints: [AGENTS.md](../../../AGENTS.md#repository-constraints) (adapter layout, preserve customizations, prefer native capabilities, 1000-line Python files with fixed legacy ceilings) and the user's lightweight rule (`~/ai-memory-vault/smart-router-lightweight.md`): on-demand work only, no timers, daemons or polling, no extra model calls, short bounded processes, cheap hot paths, and every added cost stated with its lighter alternative.

## User decisions (2026-11-10)

| # | Question | Decision | Where applied |
| --- | --- | --- | --- |
| 1 | Orientation mechanism | The plugin `context` hook | [A](#a-orientation-plugin-gap-a) |
| 2 | Which sessions are oriented | Main (root) sessions only | A, step 1 |
| 3 | Distribution | A local path to the package (clean export or release copy) | [Installation](#installation-preparation-and-onboarding-stay-distinct) |
| 4 | Skills | **Registered by the plugin** (`ctx.skill.transform`); the global config gets only the plugin entry. If registration fails, readiness shows skills unavailable. No silent fallback to a config edit without the user | [C](#c-skills-gap-c) |
| 5 | Worker guidance coverage | Subscription providers only, and only those actually connected in the user's OpenCode. Pay-as-you-go models are never offered, except OpenRouter through the OpenRouter Integration feature (within Smart Routing limits). Detection is lightweight and on demand, with no model call | [D](#d-worker-selection-gap-d) |
| 6 | Per-call `subagent` `model` | Expected to work (the user has seen agents spawn other models and efforts). If the live check shows it does not, record the limitation. No config agents | D |
| 7 | Compaction prompt | Untouched | A |
| 8 | Managed launches (coordinator decision) | They must never load the workflow plugin. The plugin goes inert when it detects a managed launch. Part B and the preparation step wait for the pre-canary global-config probe | [G](#g-managed-launches-decision-8) |
| 9 | Live check that edits the real config (Part B) | Approved; still waits for the pre-canary probe (decision 8) | [Part B](#part-b-real-desktop-session-one-model-call-approved-gated) |
| 10 | Scope | **Not decided.** The user asked for a feature × adapter comparison first. Proposal: the Claude Code level | [Comparison](#feature--adapter-comparison) |

## Current state against Codex and Claude Code

| Capability | Codex | Claude Code | OpenCode today |
| --- | --- | --- | --- |
| Startup registration | `SessionStart` in [hooks/codex.json](../../../hooks/codex.json) (lines 3–14), registered by [.codex-plugin/plugin.json](../../../.codex-plugin/plugin.json) line 6 | `SessionStart` in [hooks/claude-code.json](../../../hooks/claude-code.json) (lines 3–14), registered by [.claude-plugin/plugin.json](../../../.claude-plugin/plugin.json) line 6 | **None.** [clients/opencode.md](../../../clients/opencode.md) line 3: "not an onboarding or startup client" |
| Re-orientation after compaction | Same hook, source `compact` | Same hook, source `compact` ([runtime/adapters/claude_code.py](../../../runtime/adapters/claude_code.py) line 7) | **None.** Only `AGENTS.md` and `instructions` load natively; nothing is enrollment-aware |
| Hook dispatch | `scripts/workflow.py` line 374 (`adapters = {"codex": …, "claude-code": …}`) | same | Rejected: "Expected a supported client and event" (lines 375–376) |
| Event normalizing | [runtime/adapters/codex.py](../../../runtime/adapters/codex.py) | [session_start.py](../../../runtime/adapters/session_start.py) lines 8–20 | [runtime/adapters/opencode.py](../../../runtime/adapters/opencode.py) covers routing hooks only |
| Orientation text | [runtime/startup.py](../../../runtime/startup.py) lines 445–458 (Codex reader variant) | lines 437–444 (generic) | The generic text would fit, but it points at `clients/<client>.md` (line 435), which for OpenCode is the 82 KB routing contract |
| Enrollment | Accepted in onboarding sources ([runtime/onboarding.py](../../../runtime/onboarding.py) line 280) | same | Refused at line 280. [managed-plan.md](../../../skills/onboard-my-project/references/managed-plan.md) line 25 and the [onboarding procedure](../../../skills/onboard-my-project/references/procedure.md) line 11 call it an explicit gap |
| Startup membership read | `runtime/startup.py` line 392 accepts any listed client | same | Already client-generic |
| Package version source | `MANIFESTS["codex"]` ([runtime/package_versions.py](../../../runtime/package_versions.py) line 18), read by `manifest_version` (line 50) | `MANIFESTS["claude-code"]` | **None.** `manifest_version` returns `None` |
| Delivery note (hook ran) | [runtime/hook_delivery.py](../../../runtime/hook_delivery.py) `current()` needs both `manifest_version` and `HOOK_FILES[client]` (line 18); `workflow.py` line 440 | same | Neither exists, so no note could be written and `drift_warning` (line 284) stays `None` |
| Installed version | `codex plugin list` (`package_versions.py` line 17) | `claude plugin list` | Absent |
| Readiness cells | [workspace-requirements.json](../../../clients/workspace-requirements.json) `package`, `hooks-trust` | same | Lines 41 and 54: `not-supported` ("pending (plan slice R4)") |
| Skills | `"skills": "./skills/"` in `.codex-plugin/plugin.json` line 5 | default `skills/` dir ([clients/claude-code.md](../../../clients/claude-code.md) line 9) | **None** |
| Worker selection guidance | Runtime-backed cache, surfaces `codex-cli`/`codex-desktop` only (`scripts/workflow.py` line 229; requires runtime state, lines 257–260) | **None** | **None.** The native `subagent` tool exists; [opencode.py](../../../runtime/adapters/opencode.py) lines 256–267 already parse a per-call `model: provider/model#variant` |
| Managed delegation | Advisory, via `prepare` | none | Managed launcher; [scripts/routing.py](../../../scripts/routing.py) lines 96–102 already list `opencode` as forward-compatible |
| Titles | Runtime title sync | — | Ordinary-path [title command](../../../clients/workstreams-titles.md#opencode-sessions) |

### The gap list, corrected

- **(a) Startup and compaction hook: confirmed.**
- **(b) Client acceptance: partly wrong.** Needed: `runtime/onboarding.py` line 280, `scripts/workflow.py` lines 374 and 440, `runtime/hook_delivery.py` line 18, `runtime/package_versions.py` lines 17–18 **and a version source** (part E), `runtime/workspace.py` (`check_package` line 90 and `check_hooks` line 144 pick a Codex or Claude parser), `clients/workspace-requirements.json`, `clients/startup.md` (`clients` field) and the onboarding skill text. **Not needed:** `runtime/state.py` (lines 1443, 1855, 2096) and `runtime/observability.py` (line 57) gate Codex task-runtime records; Claude Code is only partly admitted there. `runtime/startup.py` is already client-generic.
- **(c) Skill packaging: confirmed.**
- **(d) Worker-selection guidance: confirmed, and Claude Code lacks it too.** The existing guidance cache needs Codex runtime state, so it cannot be reused for a startup-only client.
- **(e) Missing from the list: the client contract file.** Startup and onboarding require `clients/<client>.md` (`runtime/startup.py` lines 405 and 435, `runtime/onboarding.py` line 281). For OpenCode that file is the Smart Routing contract (350 lines, 82 KB). Pointing every session at it would cost about 20,000 tokens and load unrelated rules.
- **(f) Missing from the list: a package version source for OpenCode.** OpenCode has no plugin manifest, so neither the delivery note nor the drift warning can work without one (part E).

## Feature × adapter comparison

The user asked for this before deciding the scope (decision 10). "Proposed" is this design; everything in the last column beyond it needs a separate decision.

| Feature | Codex | Claude Code | OpenCode, proposed | OpenCode, not proposed |
| --- | --- | --- | --- | --- |
| Startup orientation in enrolled checkouts | Yes | Yes | Yes (plugin `context` hook) | — |
| Re-orientation after compaction | Yes | Yes | Yes (`compaction` hook marks, next request reruns) | — |
| Packaged skills | Yes | Yes | Yes (plugin registration) | — |
| Enrollment through onboarding | Yes | Yes | Yes | — |
| Readiness, version and drift warnings | Yes | Yes | Yes | — |
| Worker-selection guidance | Runtime cache | None | Packaged section plus a connected-subscription reader | Runtime cache |
| Managed delegation (Smart Routing) | Advisory | None | Managed launcher (exists) | — |
| Pre-tool patch guard, post-tool notice, `Stop`/`Interrupt` | Yes, opt-in runtime | No | No | Possible through `tool.execute.before/after` and `session.idle`; needs task state |
| Task state, evidence and review runtime | Yes, opt-in | Partial | No | Would need `runtime/state.py` and `observability.py` changes |
| Runtime title sync | Yes | No | Ordinary title command (exists) | Runtime-bound sync |
| Bounded instruction reader | Yes (`functions.exec` loader) | No | No (native `read`) | — |

## Design

### A. Orientation plugin (gap a)

**Mechanism.** One new OpenCode V2 server plugin, `runtime/adapters/opencode_workflow.ts`, id `vibe-workflow.workflow`. It has no dependencies, holds no policy and stays separate from the routing shim. It uses only documented V2 APIs (OpenCode docs v2.1.2 through Context7: `build/plugins/index.mdx`, `migrate-v1.mdx`, `skills.mdx`):

- `ctx.session.hook("context", …)`: runs right before each agent-loop model request, including tool-driven continuations, but not for `title`, `generate` or `compaction` requests. The handler appends the cached orientation to `event.system`.
- `ctx.session.hook("compaction", …)`: marks that session for re-orientation. It never sets `result` and never edits the compaction prompt (decision 7).
- `ctx.session.get(id)`: read once per session to learn the parent and directory.
- `ctx.skill.transform(…)`: part C.

It does not subscribe to the event stream, uses no timer and never logs. In a managed launch it does nothing at all (part G).

**Flow, per session and plugin instance:**

1. The first `context` call for a session decides eligibility once. Only main (root) sessions are oriented (decision 2), so a session with a parent is skipped. A session whose ID has the ledger-derived shape (`ses_` plus 32 lowercase hex) is also skipped, as a second guard behind part G. A failed `session.get` counts as a root, because orientation is guidance only.
2. For an eligible session, the plugin runs `python3 -I -B <package>/scripts/workflow.py opencode SessionStart` once. Stdin gets `{hook_event_name:"SessionStart", session_id, cwd, source, skills}`:
   - `cwd` is the session's directory.
   - `source` is `startup`, or `compact` after a compaction.
   - `skills` is the registration outcome from part C.

   The package root comes from `import.meta.url`, as in the routing shim's `defaultArgv`. A `python` plugin option overrides the interpreter, because desktop apps often get a minimal `PATH` whose `python3` is the macOS 3.9 stub.
3. The plugin caches the result per session: the `additionalContext` text, a diagnostic, or "nothing" (unenrolled). It keeps at most 256 sessions and evicts the oldest. Concurrent first calls share one in-flight promise.
4. Every later `context` call appends the cached text when there is one. The text is byte-stable, so provider prompt caching applies.
5. A `compaction` call clears that session's cache entry and sets `source` to `compact`, so the next `context` call reruns step 2.

**Python side.** The new module `runtime/adapters/opencode_startup.py` follows the `claude_code.py` pattern:

- `SOURCES = {"startup", "compact"}`. It reuses `session_start.normalize`, `render` and `diagnostic`.
- It validates the optional `skills` field: `{registered, expected}`, two integers from 0 to 64.
- For `compact` it adds one fixed sentence: "Context was compacted: restore missing instructions and check actual project state before continuing" (policy 07, handoff and resumption).

`scripts/workflow.py` registers `"opencode"` in `adapters` and in the delivery-note set. `runtime/startup.py` is not changed: it is at its 1515-line ceiling, and its generic orientation already fits.

**What restores state after context loss.** System text is not part of the transcript, so compaction never removes it. The orientation is present on every agent request before and after compaction. The rerun after compaction refreshes it (enrollment and delivery note) and adds the restore sentence. That matches what Codex and Claude Code get from `SessionStart` `compact`. No task state is injected: startup-only scopes have none, as on Claude Code.

**Resume.** OpenCode has no resume event. A restarted server is a new plugin instance with an empty cache, so the first request of a resumed session reruns step 2 with `startup`. That is equivalent, because `orient` does not depend on the source.

### B. Enrollment (gap b)

- `runtime/onboarding.py` line 280 accepts `"opencode"`. The edit replaces a tuple and adds no lines; the file is at its legacy ceiling. **It lands in S4, together with the client contract and the preparation step**, so onboarding never enrolls OpenCode before a session could be oriented correctly (review S8).
- `clients/startup.md` lists `opencode` among the `clients` values. [managed-plan.md](../../../skills/onboard-my-project/references/managed-plan.md) and the [onboarding procedure](../../../skills/onboard-my-project/references/procedure.md) step 1 lose the "until its plugin client ships" gap.
- Canonical startup-only membership (`configured`, `runtime:null`) is written by the existing file-configuration path, as for Claude Code ([policy 10](../../../policies/10-project-onboarding.md#client-startup-integration)).
- [Policy 03](../../../policies/03-agent-delegation.md#managed-projects) "Onboarding records only Codex and Claude Code today…" and the `scripts/routing.py` comment become stale; S4 updates them.

### C. Skills (gap c)

**Mechanism (decision 4).** At `setup`, unless the launch is managed (part G), the plugin reads `<package>/skills/*/SKILL.md`: nine files of about 1 KB, bounded to 64 files of 16 KiB each. It parses the frontmatter `name` and `description` and adds each skill with `ctx.skill.transform(editor => editor.add({id, name, description, path, content}))`, using the absolute `path` of the `SKILL.md`. It does not override a skill another source already registered under the same id. It counts that skill as not registered, so the collision shows in readiness. The global config gets only the plugin entry.

**Reporting.** The outcome `{registered, expected}` travels in the SessionStart payload. The OpenCode adapter stores it in the OpenCode delivery note. In `workspace status`, the OpenCode `vibe-workflow-plugin` cell is `ready` only when the note's version matches and `registered == expected`. Otherwise the cell is `missing` with the reason "skills unavailable (N of M registered)".

**Fail-safe.** No silent fallback: preparation never adds a `skills` config entry on its own. When skills are unavailable, it reports the cell and offers the config entry as a user action that needs the user's approval.

Unobserved, so the live qualification must check them: that `ctx.skill.transform` exists on 2.2.6 and the native `skill` tool loads a registered skill; that relative links resolve from the reported `path`; and whether a transform at `setup` needs `skill.reload()`.

### D. Worker selection (gap d)

The local guidance cache in policy 03 needs Codex runtime state (`scripts/workflow.py` lines 257–260), which a startup-only client does not have. The lightweight equivalent is a **packaged guidance section** in the new short `clients/opencode.md`. It names the OpenCode dispatch controls, the official source URLs, the checked date, the OpenCode version applicability and a SHA-256 digest per source. Workflow maintenance (policy 09) refreshes it on the policy's triggers, never in a hook.

- **Coverage (decision 5).** It covers official model guidance for **subscription providers only**: on this machine the Claude Agent provider, the ChatGPT sign-in route and OpenCode Go. It offers a provider only when that provider is connected in the user's OpenCode at dispatch time; a user with only ChatGPT gets only ChatGPT. Pay-as-you-go models (API-key providers, OpenCode Zen pay-per-use, any other metered route) are never offered. The one exception is OpenRouter, and only through the [OpenRouter Integration feature](../../../skills/openrouter-workers/SKILL.md), within its saved enablement and Smart Routing limits; never as a native `subagent` model.
- **Connected-subscription detection (lightweight).** A new `scripts/opencode_providers.py` (with `runtime/opencode_providers.py`) is run by the coordinator only when it is about to select a worker:
  - It makes one bounded read of the server's provider listing through `opencode api`, the transport `runtime/opencode_titles.py` already uses: one process, 2 s timeout, no retry loop, no model call. The exact operation is confirmed in the live check.
  - It intersects the connected providers with a packaged allowlist of subscription routes, matched by provider id and authentication kind (`oauth`/subscription sign-in, never an API key).
  - It prints only provider ids. It never opens `auth.json` and never prints account data.
  - The result is cached for the session (a private file keyed by session id, at most 15 minutes), so later dispatches reuse it.
  - Fail-safe: an unreadable listing or an unknown authentication kind excludes that provider and says why. Dispatch then falls back to inheritance (the parent's model) with the limitation recorded.
- **Dispatch control.** The `subagent` tool takes `agent`, `description`, `prompt`, optional `background`, and `sessionID` to continue. The per-call `model: "provider/model#variant"` is not in the 2.1.2 docs. The adapter already parses it (`runtime/adapters/opencode.py` lines 256–267), and the user has seen agents spawn other models and efforts (decision 6). The live check confirms it. If the live check shows it is ignored, the guidance records the limitation and uses inheritance; **no config agents are added**. Global worker agents would also trip the managed launcher's `agent_steps_uncapped` refusal (`uncapped_agents`), because the inline config does not cap agents it does not name.
- **Preserve the coordinator.** Never write the top-level `model` or `small_model`, a primary agent (`build`, `plan`) or a user-defined agent. Choose worker settings per call.
- **Record** requested settings (the spawn input) separately from observed ones (the child's stored assistant-message `model`, readable through `opencode api`).
- **Managed projects:** unchanged. OpenCode workers go through the [managed launcher](../../../clients/opencode.md#managed-launcher), and a native `subagent` in a managed project is not a routed dispatch.

### E. Readiness, version and delivery (gap f)

- **Version source (review B1).** A new package manifest, `.opencode-plugin/plugin.json`, with `name`, `version` and `plugin: "./runtime/adapters/opencode_workflow.ts"`, sits next to `.codex-plugin/` and `.claude-plugin/`. OpenCode does not read it; only the package's Python does.
  - `MANIFESTS["opencode"] = ".opencode-plugin/plugin.json"`. `manifest_version(package, "opencode")` then works, and `package_version` requires all three manifests to agree.
  - `scripts/validate.py` checks that its version equals the other two and that its `plugin` path exists. The release version bump touches three manifests; `docs/release.md` is updated in S2.
- **Delivery note:** `HOOK_FILES["opencode"] = "runtime/adapters/opencode_workflow.ts"`, so `hook_delivery.current()` returns the manifest version and the plugin file's hash. The OpenCode note also records the optional `skills` outcome (part C). `workspace status` reports `vibe-workflow-hooks` `ready` for OpenCode only from a fresh note. There is no trust approval to observe: OpenCode loads configured plugins without one.
- **Drift warning:** `drift_warning(package, "opencode")` compares the running package's OpenCode manifest with the machine record, as for the other clients.
- **Installed version:** `package_versions.observe("opencode")` reads the global `opencode.json`/`.jsonc` with the existing `workspace_observe.opencode_config_plugins` and finds the entry that points at a package's `runtime/adapters/opencode_workflow.ts`. It reads that package's `.opencode-plugin/plugin.json` version. This needs **no subprocess**, which is cheaper than the Codex and Claude CLI listings.
- `clients/workspace-requirements.json`: the OpenCode cells of `vibe-workflow-plugin` and `vibe-workflow-hooks` become `package` and `hooks-trust`.

### F. Client contract file (gap e)

**Recommendation:** rename the routing contract to `clients/opencode-routing.md` and make `clients/opencode.md` a short client contract (startup behavior, installation, skills, worker guidance), like `clients/claude-code.md`. Then `clients/<client>.md` keeps one meaning and `runtime/startup.py` stays unchanged.

The rename touches 89 occurrences in 21 files (`grep -rno opencode.md`). Open PR #241 touches two of them, `tests/qualification/opencode_live.py` and `docs/smart-routing-qualification.md` (its runbook steps 7–8), so the rename waits until #241 merges.

Alternative: a client-file mapping in `runtime/startup.py` and `runtime/onboarding.py`. That avoids the rename, but adds a rule and lines to two files at their ceilings.

### G. Managed launches (decision 8)

Managed launches must never load the workflow plugin. The launcher's inline config has the routing shim as its only plugin, but it keeps the user's configuration directories, so a global `plugins` entry may still load next to it. That is the open pre-canary requirement ([clients/opencode.md](../../../clients/opencode.md#managed-launcher), "Pre-canary requirement").

- **Detection.** The launcher adds one fixed marker, `VIBE_WORKFLOW_MANAGED_LAUNCH=1`, to its server environment (in `SERVER_FLAGS`, `runtime/opencode_launcher.py` line 68). As a second signal, `OPENCODE_CONFIG_CONTENT` lists the routing shim (`runtime/adapters/opencode_plugin.ts`). When either is present, or `OPENCODE_CONFIG_CONTENT` is present but unparseable, `setup` returns immediately: no hook, no skill registration, no process, no file read beyond the environment.
- **Exact residual.** If the probe shows global plugins load in managed launches, OpenCode still imports the workflow plugin module and calls its `setup`, which returns at once. The routing shim stays the only plugin that registers anything; the statement "the routing shim is the only plugin" is then true for behavior but not for loading. If the probe shows global plugins do not load, there is no residual. The derived-ID skip in part A is a second guard only.
- **Gating.** Part B of the live qualification and the S4 preparation step (which adds the global plugin entry) wait for the pre-canary probe result on global-config loading. If the probe fails, the launcher's planned fallback (private configuration directories) also removes the residual. Until then the plugin is not installed into a real global config.

## Installation: preparation and onboarding stay distinct

| Stage | Owner | What it does for OpenCode |
| --- | --- | --- |
| Environment preparation (machine/user scope) | `prepare-my-workspace` step 4 | Waits for the pre-canary probe (part G). Backs up the global config file in the run directory (step 3). Adds **one** entry: the plugin, as an absolute path to the package's `runtime/adapters/opencode_workflow.ts`, with `options.python` when the default interpreter is too old. Step 6 needs a new OpenCode session. Step 7 records it. |
| Project onboarding | `onboard-my-project` | Step 0 runs `workspace status`. Missing OpenCode plugin or hook cells are announced and handed to preparation, then onboarding resumes; it never edits client config itself. Step 5 writes startup-only canonical membership for `opencode`. Step 6 verifies orientation in a new OpenCode session. It writes no project `opencode.json`. |

The package path is the clean export (`~/.cache/vibe-workflow/dev-package` on this machine, [setup record](../../setup.md#client-integration)) or a release copy, never a client cache (decision 3). An upgrade refreshes that directory, so the entry follows it in one step.

## Preserving the user's OpenCode configuration

Edit-in-place rules for the global `opencode.json` or `opencode.jsonc`:

- Prefer the native CLI (`opencode plugin add <path>`) when 2.2.6 accepts a local path; otherwise make a minimal textual insertion that keeps comments, order and formatting. Never re-serialize the whole file.
- Append only. Never reorder, rewrite or remove another `plugins`, `plugin` (V1 key), `skills`, `instructions`, `mcp`, `agents`, `permissions`, `model` or `small_model` entry. If the file uses the V1 `plugin` key and 2.2.6 does not read both keys, stop and leave the cell `needs-user` with the exact snippet.
- Skip an entry that already names the same package (idempotent). A different `vibe-workflow` copy is `outdated`; the upgrade procedure replaces only that entry.
- Undo: restore the backup only while the file still matches the post-edit digest; otherwise remove exactly the inserted entry and report it. Record the path, digests and undo step in the [setup record](../../setup.md#maintenance-and-undo).
- Never touch credentials (`auth.json`), the desktop app's state or project config files.

## Failure modes

| Failure | Behavior | Kind |
| --- | --- | --- |
| Checkout not enrolled for `opencode` | Python prints nothing; the plugin caches "nothing" and injects nothing | Silent, by design |
| Managed launch detected | `setup` returns at once; nothing registered | By design (decision 8) |
| `python3` missing or older than 3.11 | `workflow.py` returns its version diagnostic; the plugin injects it as system text for that session | Degrade: session works, workflow readiness not claimed |
| Spawn error, non-zero exit, malformed or oversized output (over 64 KiB) | Fixed diagnostic text ("Vibe Workflow startup unavailable: …"), cached for the session | Degrade |
| Timeout (5 s hard; process group SIGTERM, then SIGKILL) | Same diagnostic; no retry until compaction or a new plugin instance | Degrade, no loop |
| Invalid enrollment or policy digest drift | Python's existing diagnostic, as on Codex and Claude Code | Degrade, reported |
| Plugin fails to load | No orientation, no skills and no delivery note, so `workspace status` stays not ready. OpenCode's load-failure behavior is unobserved (capability matrix P0.6) | Visible through readiness |
| Skill registration fails or collides | Skills unavailable; the plugin cell reports "skills unavailable (N of M registered)"; no config fallback without the user | Visible through readiness |
| `session.get` fails | Session treated as root and oriented | Degrade toward guidance |
| Provider listing unreadable | No subscription provider offered; inheritance with the limitation recorded | Fail closed for offering models |
| Config edit unsafe (V1 key, unparseable JSONC) | No edit; `needs-user` with the snippet | Fail closed for the edit |

Orientation never blocks a session or a tool. The workflow's claims fail closed: without an observed orientation, readiness is not claimed.

## Cost under the lightweight rule

| Piece | Added cost | Lighter alternative |
| --- | --- | --- |
| Plugin load | No process and no event subscription. Nine small `SKILL.md` reads (decision 4). Inert in managed launches | — |
| Orientation, first request of each main session | One short Python process (bounded 5 s; typical startup plus Git lookup well under 1 s), on the critical path of that one request | A native `instructions` entry: zero processes, but no enrollment check, no diagnostics, delivery note or drift warning (not chosen; decision 1) |
| Orientation on later requests | `context` runs on **every agent-loop request, including each tool-driven continuation**. Each such step carries about 200 tokens of byte-stable system text, normally served from the provider's prompt cache. A long tool loop of N steps sends it N times | The `prompt` hook: inject once into the first user prompt, and again at the next prompt after a compaction mark. That still restores orientation after compaction. It is **not lighter in tokens**: the text sits in the transcript, which is resent on every request until compaction summarizes it. Its differences are placement (the stored, visible user message, which the plugin would then alter) and fewer hook calls (in-memory only). It also leaves a gap between a compaction and the next user prompt |
| Compaction | One more Python process per compaction | Skip the rerun and keep the cached text, losing only the refresh and the restore sentence |
| Child sessions | One in-process `session.get` per session; no process; no tokens | — |
| Worker guidance | One bounded `opencode api` process when a worker is about to be selected, cached up to 15 minutes per session; no model call | A packaged list of every subscription route without the connected check, which could offer providers the user cannot use |
| Readiness and version | Two small config-file reads, no subprocess | — |
| Delivery note | At most one small write per 24 h, inside the hook deadline | — |
| Model calls | **None** | — |

## OpenCode 2.2.6 dependencies

Each row is a documented (2.1.2) or recorded behavior this design relies on, what happens if 2.2.6 differs, and the check that settles it. The live qualification runs every check.

| Behavior relied on | If 2.2.6 differs (fail-safe) | Live check |
| --- | --- | --- |
| `session.hook("context")` additions to `event.system` reach the request, once per request | No orientation; delivery note still shows the spawn ran; readiness stays not ready | A1 step 3: the stub's recorded system text contains the orientation exactly once |
| `context` does not run for `title`, `generate` or `compaction` requests | Extra tokens on auxiliary requests only | A1 step 6: the stub's title and compaction requests carry no orientation |
| `session.hook("compaction")` runs before the summary | No refresh after compaction; the system text remains | A1 step 6: the request after compaction carries the restore sentence |
| `session.get` returns the parent for child sessions | Children oriented too (extra tokens) | A1 step 5: the child's request carries no orientation |
| `ctx.skill.transform` exists and the `skill` tool loads registered skills | Skills unavailable, shown in readiness; no config fallback without the user | A1 step 4 |
| Per-call `subagent` `model` is honored | Inheritance with the limitation recorded; no config agents | A1 step 5 |
| The provider listing through `opencode api` names connected providers and their authentication kind | No subscription provider offered; inheritance | A1 step 7 (stub listed; nothing else) and B step 2 (connected subscriptions on this machine) |
| A local-path `plugins` entry loads (V2 key; V1 `plugin` handling) | Preparation leaves `needs-user` with the snippet | A1 step 2 (V2 key), B step 1 |
| Global config loads in managed launches | Plugin inert by detection; residual stated in part G | Pre-canary probe (Smart Routing); A1 step 8 |
| The desktop app's `PATH` reaches Python 3.11+ | Version diagnostic; `options.python` fixes it | B step 2 |
| Plugin load-failure behavior | Readiness stays not ready | Not checked live (destructive to provoke); stays unobserved |

## Shared-core touch points (full suite)

`scripts/workflow.py`, `runtime/onboarding.py`, `runtime/hook_delivery.py`, `runtime/package_versions.py`, `runtime/workspace.py`, `runtime/opencode_launcher.py` (one marker), `clients/workspace-requirements.json`, `scripts/validate.py`, `policies/03-agent-delegation.md` and the instruction map.

A policy edit changes the legacy schema-1 `policy_digest`, so legacy-enrolled checkouts get the "Installed policies differ" diagnostic until they are re-accepted. **Exactly one slice (S4) edits a policy**: it carries both the rename's link fix and the content change. Each slice that touches these files runs the full `scripts/test_sharded.py` once on its committed head, as the development guide requires.

## Slice plan

Each slice writes its tests first, has non-overlapping owned files and can merge on its own. Whichever slice merges later rebases and regenerates `clients/instruction-map.json`.

**S1–S3 are inert until S4 lands.** Onboarding refuses `opencode` until S4, and no procedure installs the plugin before S4's preparation step. So no user session runs the new code paths until then.

| Slice | Owns | Test first | Depends on | Suite |
| --- | --- | --- | --- | --- |
| **S1 Python adapter** | `runtime/adapters/opencode_startup.py` (new), `scripts/workflow.py` (adapter and note set), `clients/startup.md`, `tests/test_opencode_startup.py` (new) | `opencode SessionStart` produces orientation in an enrolled fixture whose membership is written directly by the test, nothing when unenrolled, and a diagnostic for a bad payload or bad `skills` field; `compact` adds the restore sentence | — | Full |
| **S2 Plugin, manifest and launcher marker** | `runtime/adapters/opencode_workflow.ts` (new), `.opencode-plugin/plugin.json` (new), `tests/opencode_workflow/*.test.ts` (new, `node --test` with a fake `workflow.py` and fake skill editor), `runtime/hook_delivery.py`, `runtime/package_versions.py` (`MANIFESTS` only), `scripts/validate.py`, `docs/release.md`, `runtime/opencode_launcher.py` (marker), `tests/test_hook_delivery.py`, `tests/test_package_versions.py` (manifest cases), `tests/test_opencode_launcher.py` (marker case) | Plugin: context injects the once-spawned text; child, derived-ID and failed-spawn cases; compaction reruns with `compact`; timeout kills the group; skills registered, collision counted, outcome sent; inert under the marker, under a routing-shim inline config and under an unparseable one. Python: `manifest_version(pkg, "opencode")` reads the new manifest; `package_version` is `None` when the three disagree; `hook_delivery.current(pkg, "opencode")` returns `(version, plugin hash)` and a SessionStart writes the note with `skills`; `drift_warning(pkg, "opencode")` warns on a mismatched record; validate fails on a version mismatch. Launcher: the server environment carries the marker | S1 | Full |
| **S3 Readiness and providers** | `runtime/package_versions.py` (`observe` for OpenCode), `runtime/workspace.py`, `runtime/workspace_observe.py`, `clients/workspace-requirements.json` and `.md`, `runtime/opencode_providers.py` and `scripts/opencode_providers.py` (new), `tests/test_workspace.py`, `tests/test_opencode_providers.py` (new) | Config-listed plugin entry gives `installed` with the manifest version; a mismatched package gives `outdated`; a fresh note with all skills gives both cells `ready`; partial skills give "skills unavailable"; nothing spawns. Provider reader with a fake `opencode`: subscription providers kept, API-key and unknown kinds dropped with reasons, one call, timeout honored, cache reused | S2 | Full |
| **S4 Contract, policy, onboarding and preparation** | `clients/opencode.md` renamed to `clients/opencode-routing.md` plus every inbound reference (21 files); new short `clients/opencode.md`; `clients/index.md`; `policies/03-agent-delegation.md` (the only policy-digest change: link fix and content); `runtime/onboarding.py` line 280; `skills/onboard-my-project/references/managed-plan.md` and `procedure.md`; `skills/prepare-my-workspace/references/procedure.md` (OpenCode install, backup and undo, gated on the probe); `docs/onboard-my-project-plan.md`; `docs/policy-sources.md`; `scripts/routing.py` comment; `clients/instruction-map.json` | Onboarding source record accepts `opencode`. Representative-task instruction checks: an OpenCode startup loads the short contract and the index, not the routing contract; a preparation run loads the OpenCode install steps; delegation loads the worker guidance; the link and anchor checks pass | S1–S3, #241 merged, pre-canary probe for the preparation step | Full |
| **S5 Live qualification record** | `docs/opencode-client-validation.md` (new), `docs/setup.md` (client integration), `tests/opencode_client_live.py` (new; skipped unless explicitly enabled) | — (observation) | S1–S4 | Static checks |

S5 avoids `tests/qualification/*`, which belongs to #241.

## Live qualification procedure

The coordinator runs this at the end from its own OpenCode 2.2.6 Desktop session, through its shell tool. Part A uses **zero real model calls** and touches no real user state. Part B makes **one** short model call and edits the real global config: the user approved it (decision 9), but it waits for the pre-canary probe.

### Part A: disposable server with a local stub provider (zero model calls)

1. **Allowlisted environment (review B2).** The coordinator's shell carries `OPENCODE`, `OPENCODE_CLIENT` and `OPENCODE_SESSION_ID`, and may carry `OPENCODE_CONFIG*` and provider keys, so never inherit it. Start the server with `env -i` and only these:
   - `PATH`: the launcher's fixed `SERVER_PATH`.
   - `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME` and `XDG_CACHE_HOME`: each under a new private temp root.
   - `USER`, `LANG` and `TMPDIR`: copied from the caller.
   - `OPENCODE_DISABLE_AUTOUPDATE=1` and `OPENCODE_DISABLE_MODELS_FETCH=1`.

   This mirrors `opencode_launcher.host_environment`. Before starting, assert that every path variable is inside the temp root, and that no `OPENCODE_SESSION_ID`, `OPENCODE_CLIENT`, `OPENCODE_CONFIG*` or credential-like name (`adapter.credential_name`) is present. The empty `XDG_DATA_HOME` holds no `auth.json`, so no real provider is signed in.
2. **Config pinned to the stub.** Write a temporary config with only:
   - the workflow plugin (V2 `plugins` key, local path to the export under test);
   - one OpenAI-compatible provider `stub` whose base URL is a loopback stub;
   - `model` and `small_model` pinned to `stub/coordinator`;
   - provider restrictions to `stub` if 2.2.6 supports them.

   The stub records every request body and returns canned replies. Make two disposable Git fixtures: one enrolled for `opencode`, one unrelated.
3. **Hook loaded.** Start `opencode serve` on loopback in the enrolled fixture, create a session through the HTTP API and send "ping". The stub's recorded request has the orientation sentence ("Vibe Workflow applies to this enrolled checkout…") exactly once in its system text. `$XDG_STATE_HOME/vibe-workflow/delivered/opencode.json` names the package version, the plugin hash and `skills` 9 of 9. In the unrelated fixture: no orientation.
4. **Skills resolve.** The stub returns a canned `skill` tool call for `onboard-my-project`. The tool result holds that `SKILL.md`, and a relative link in it resolves from the reported path.
5. **Worker selection follows guidance.** The stub returns a canned `subagent` call with `model: "stub/worker"` while the session runs `stub/coordinator`. The stub sees the child's request on `worker` with **no** orientation text (child skipped), and the parent's next request still on `coordinator`. The config file is byte-identical afterwards.
6. **Compaction restores state.** Trigger compaction through the session API. The stub gets the compaction request with the prompt unchanged by the plugin and with no orientation. The next agent request carries the orientation plus the restore sentence.
7. **Provider reader.** Run `scripts/opencode_providers.py` against this server. It lists only `stub`, and only if `stub` is a subscription route. An API-key stub is excluded with its reason.
8. **Managed-launch inertness.** Restart the server with `VIBE_WORKFLOW_MANAGED_LAUNCH=1`. A session request carries no orientation, the skill listing has no packaged skills, and no `workflow.py` process starts (the delivery-note time does not change).
9. **Every request went to the stub.** Every stored assistant message names provider `stub`. The number of requests the stub recorded equals the agent, title and compaction requests the session store shows. Any request to another provider, or any assistant message with an authentication or network error, fails the check.
10. **Undo.** Stop the server's process group and delete the temp root. No real file changed.

Prerequisite and limit: OpenCode must load an OpenAI-compatible provider offline. If 2.2.6 needs to fetch the provider package, record that and move steps 3–7 to Part B. The step-9 assertion still applies to whatever does run.

### Part B: real Desktop session (one model call, approved, gated)

Runs only after the pre-canary probe settles global-config loading (decision 8).

1. Run `prepare-my-workspace` for the OpenCode cells. It backs up the global config and adds the plugin entry. Then onboard a disposable checkout for `opencode`.
2. Open a **new** Desktop session in that checkout and send one short prompt: "Name the file the Vibe Workflow startup context told you to read first." That is one call on the coordinator's own model. Then check:
   - the answer names the policy index or client file;
   - the delivery note is fresh;
   - `python3 -I -B scripts/workflow.py workspace status --client opencode` shows the plugin and hooks cells `ready`;
   - the session's skill list shows the packaged skills;
   - `python3 -I -B scripts/opencode_providers.py` lists exactly the connected subscription providers (Claude, ChatGPT, OpenCode Go on this machine).
3. Undo: the preparation run's rollback restores the config backup (or removes exactly the inserted entry), and the disposable checkout's onboarding rollback removes its membership.

Report observed client execution separately from package and fixture checks.

## Open decisions

- **User, decision 10 (scope):** the Claude Code level as proposed, or more of the [comparison](#feature--adapter-comparison)'s last column.
- **Coordinator:** the contract layout, either the S4 rename (recommended) or a client-file mapping. After that, mark draft PR #190 superseded.

## Not yet observed

Everything about 2.2.6 in this design comes from the 2.1.2 documentation or the repository's recorded probes, not from a 2.2.6 run of this plugin. The [dependency table](#opencode-226-dependencies) lists what the live qualification must observe.

## Review history

- **2026-11-10, independent review of `8ad9c46`: do not merge.** About 20 citations were verified, the gap-list corrections were confirmed, and the slice plan was judged sound. Findings and their resolution in this revision:
  - **B1 (blocking):** `hook_delivery.current()` needs `manifest_version` through `MANIFESTS`, and OpenCode had none, so no note and no drift warning. Fixed: gap (f), the `.opencode-plugin/plugin.json` version source in part E, added in S2 with tests.
  - **B2 (blocking):** Part A inherited the coordinator's `OPENCODE*` and provider variables. Fixed: allowlisted `env -i` environment, model pinned to the stub, and the every-request-went-to-the-stub assertion (Part A steps 1, 2, 9).
  - **S3:** global plugins may load next to the launcher's inline config. Fixed: part G (managed-launch detection, exact residual, gating of Part B and preparation).
  - **S4:** global worker agents would trip `uncapped_agents`. Noted in part D.
  - **S5:** one policy-digest change only. Fixed: the former S4a and S4b merged into S4.
  - **S6:** honest `context` cost and an accurate `prompt`-hook alternative. Fixed in the cost table; the "restore guarantee is lost" claim was false and is removed.
  - **S7:** a 2.2.6 dependency table. Added.
  - **S8:** S1–S3 inert until S4. Stated, and onboarding acceptance moved into S4.
  - **Nits:** Part B uses `python3 -I -B`. Rename counts measured (21 files, 89 occurrences). #241 touches `docs/smart-routing-qualification.md` (runbook steps 7–8), so S4 waits for #241.
- **2026-11-10, user decisions** recorded in [User decisions](#user-decisions-2026-11-10) and applied: skills registered by the plugin (C), subscription-only connected-provider guidance (D), no config agents (D), compaction prompt untouched (A), Part B approved but gated (Part B), scope pending a comparison (added).
