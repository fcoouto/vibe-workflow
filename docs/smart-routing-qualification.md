# Smart Routing live qualification on OpenCode 2.2.6 (9 November 2026)

**Live probes (harness 85ae5f4): `descendants` and `s33d` both passed with 7 calls (`qualification-2` 7 of 10).** The `` !`env` `` probe found that a command template's shell does not reach `shell.create.before`, so the dummy credential-like variable was visible. See [the run](#live-probe-run-descendants-and-s33d-harness-85ae5f4). The ChatGPT run has not run.

**Re-run outcome (harness at 3f87159): stages 0–4 completed with no blocker.** There is one deviation (below). The run used 4 calls, so the durable ledger stands at **9 of 14** qualification calls spent and **0 of 24** pilot calls. The [re-run](#re-run-stages-04-harness-3f87159) section has the results. The first run, further below, stopped in stage 3 on a blocker that #146 fixed on `main`. Nothing here closes a gate by itself; the [capability matrix](smart-routing-capability-matrix.md#live-re-qualification-9-november-2026) records what changed.

## Live probes: descendants, S3.3d and the ChatGPT route (pre-registered, not yet run)

These probes are fixed here **before any run**. No numbers in this section change after a run starts; results go in the "Observed" lines. The existing qualification (9 of 14) and pilot (23 of 28) budgets and thresholds are untouched.

**Approvals and budgets.** The user approved two new durable purposes, recorded on 9 November 2026 as cap lines with an approval record (who, when, scope) in `~/.local/state/vibe-workflow-qualification/calls.jsonl`:

| Purpose | Cap | Route | Funding |
| --- | --- | --- | --- |
| `qualification-2` | 10 | `claude-agent/haiku#low` only | Claude subscription login in the isolated server (pool `sub`, account `anthropic:qual2`) |
| `chatgpt` | 4 | one `openai` model and variant, chosen by the operator from the ChatGPT login's own model list | "Sign in with ChatGPT" inside the isolated server (pool `chatgpt-sub`, `included_subscription`) |

No API keys, API Usage, organization or OpenRouter. A run uses one purpose, and the recorder guard allows only that purpose's route. The `qualification-2` and `chatgpt` purposes are closed in code (cap 0) until their approval lines exist. Every send is reserved in the durable ledger first. Each run first proves the plugin chain with a guard probe (two exposures, normally 0 sent) and stops if the probe fails.

### Stage `descendants` (`qualification-2`, at most 5 calls)

Purpose: the required live observation of `routing.descendants` (#152, [clients/opencode.md](../clients/opencode.md#plugin-rpc)).

- **Positive run.** `prepare`, `health`, `dispatch`, `attach`, and only then create the derived root (deny everything except `subagent`). Then:
  - (a) A prompt asks the model to call the subagent tool once on `claude-agent/haiku#low`, at most 3 calls: root, child, root continuation. Whether the model calls the tool is not deterministic, so a missing tool child is recorded as `unobserved` and is not a failure.
  - (b) The `qualchild` `subagent: true` command child, 1 call. The parent's follow-up turn is held by the guard (`guard_hold`, 0 tokens).
  - (c) A child created through the API with `parentID`, 1 call.

  After idle: `routing.descendants`, accounting with `shim_descendants` (cross-check on), `record complete`, `detach`, and `descendants` again.
- **Negative run (0 calls).** The derived root is created before `attach`. `descendants` and accounting run on it. The API-route semantics are checked offline on the positive run's re-read (no request).
- **Pass:**
  - the positive answer is `complete: true` with no `reason`, and its sessions and parents equal the launcher's own listing;
  - the command and API children are present;
  - accounting has no `shim_*` unknowns;
  - settlement is `window`, and `detach` succeeded;
  - `descendants` after `detach` is refused;
  - the negative answer is `complete: false` with `root_creation_unobserved`, and its accounting is flagged `shim_lineage_incomplete`;
  - routing `status` is clean afterwards.
- **Recorded either way:** whether OpenCode emitted the root's `session.created` after `attach`, how many such events there were, and any event-stream error. For the offline API-route check, all costs must be null. claude-agent reports cost 0, which nulls API costs by itself, so this check cannot tell a correct shim answer from that.
- **Observed (9 November 2026, harness 85ae5f4): pass.** All three children were observed and the shim answer equalled the listing; the negative run behaved as expected; 5 calls. See [the run](#live-probe-run-descendants-and-s33d-harness-85ae5f4).

### Stage `s33d` (`qualification-2`, at most 4 calls)

Purpose: the S3.3d required live probe ([clients/opencode.md](../clients/opencode.md#image-generation-and-configuration-edits-s33d)).

- **Edit probe (at most 3 calls).** The attached root denies everything except `edit`, so shell, MCP, `execute`, `webfetch`, `websearch` and image generation stay denied. One prompt asks the model to write three files:
  - `.opencode/command/x.md` (a config path);
  - `sub/opencode.json` (a config name in a subdirectory, chosen so a shim failure cannot reload the harness's own root `opencode.json`);
  - `agents/ok.txt` (ordinary, allowed when the session directory is not a config directory).
- **`` !`env` `` command (0 calls; the command's prompt is held).** The `qualenv` command runs under the attached root with a dummy credential-like variable `QUAL_PROBE_TOKEN`. Only the marker `QUAL_PROBE_PRESENT` or `QUAL_PROBE_ABSENT` is recorded, never the value.
- **Image generation.** Probed only if the isolated server lists a claude-agent image model. Otherwise it is `unobserved`.
- **Pass:**
  - at least one config path was attempted, and every attempted config path has at least one `deny` and no other effect; neither config file exists afterwards;
  - `agents/ok.txt` was attempted, allowed and written, and it is the only change in the probe project (snapshot before and after);
  - settlement is `window`, `detach` succeeded, and `status` is clean.
- **Not a pass:** if the model attempts no config path, the config-edit probe is `unobserved`. If it never attempts `agents/ok.txt`, the ordinary-edit probe is `unobserved`. The run then records what it saw, by shape only.
- **Matching the resources.** A permission resource names a probe file when it is the relative path, or an absolute path inside the probe project that is that path relative to the project.
- **Recorded, not judged:**
  - each evaluated edit's resource shape: relative or absolute, inside the probe project or not, config path or not, which probe file or `other` (never the path itself). This shows whether `ctx.location.directory` is the base for relative paths;
  - per config file: attempts and denials;
  - the `env` marker (present or absent), the `shell.create.before` records, and the number of image models listed.
- **Observed (9 November 2026, harness 85ae5f4): pass.** Both config paths were attempted and denied, and the ordinary edit was written; the `` !`env` `` marker was **present** and no shell hook fired; image generation unobserved; 2 calls. See [the run](#live-probe-run-descendants-and-s33d-harness-85ae5f4).

### Run `chatgpt` (`chatgpt`, at most 4 calls; its own run)

- **Sign-in (0 calls).** The harness does not connect the Claude login in this run, but the server keeps the user's `HOME`, so that login remains reachable. A Claude request is still blocked before transmission: the guard's control file allows only the chosen `openai` route, and no route at all until the model is checked. The harness checks that the `openai` integration offers `chatgpt-token-sharing` (OAuth, "Sign in with ChatGPT") and starts it with `POST /api/integration/openai/connect/oauth`. It prints only the authorization URL and instructions and writes them to `chatgpt-login.json` (mode 0600) in the isolated directory. The user completes the sign-in in their own browser. The harness polls the attempt (bounded, default 900 s), cancels the attempt on timeout and never prints a token. The login lives only in the isolated directory and is deleted with it. Cleanup deletes only the local copy and does **not** revoke the session on OpenAI's side; the user may revoke it at chatgpt.com afterwards. `--keep` is refused with `--chatgpt`, because a kept directory would keep OpenCode's stored login (an OAuth record with access and refresh tokens).
- **Model (0 calls).** `--chatgpt-model openai/MODEL[#VARIANT]` must be in the login's `GET /api/model` list. Without `#VARIANT`, the model's lowest listed variant is used (order `none`, `minimal`, `low`, `medium`, `high`, `xhigh`). The openai plugin removes `maxTokens`, so output length is not capped and the variant is the only lever. If no listed variant is in that order, the operator must name one. The model needs a second listed variant for the guard probe. Anything else is refused before a request.
- **`--chatgpt-model auto` (added 9 November 2026, before the run).** The user delegated the model choice to the coordinator. The model list exists only after the interactive sign-in, so the harness picks one model after the listing (0 calls), by this fixed rule:
  1. **Candidates:** models listed under the `openai` provider only, and only chat models:
     - `type` absent or `chat`;
     - `text` in `capabilities.input` and `capabilities.output` when those lists are present;
     - not `enabled: false` and not `deprecated`;
     - no name matching image, embed, audio, transcri, tts, speech, realtime, moderation, search-preview, deep-research, computer-use, dall-e, whisper or sora;
     - at least **two** variants (the guard probe needs a second), at least one of them in the known variant order.
  2. **Cheapest first:** sort by the untiered `cost` entry's input + output per million. A missing or **zero** cost counts as unknown and sorts last, because a subscription login may report every cost as 0.
  3. **Tie-breaks:** an id containing `nano`, then `mini`, then `small`; then the id itself, alphabetically.

     A model with a known cost always beats one with an unknown or zero cost, even when the known cost is high. That is acceptable on a subscription route, which bills no per-request cost.
  4. **Variant:** the lowest variant, by the order above.
  5. **Refusal:** if there are no candidates, or the pick has neither a cost nor a name hint, the run refuses and exits nonzero. It prints only the candidate ids, and nothing has been reserved or sent; the operator must then pass `--chatgpt-model openai/MODEL`.

  The harness prints `chatgpt model selected: openai/<id>#<variant> (rule: …)` before reserving any call, and writes it to the sign-in state file. The rule, the candidate ids, the excluded ids with reasons and the costs go into the sanitized results (`model_selection`); on a refusal, the same record with `selected: null`. After the announcement the run proceeds by itself: the only veto is Ctrl-C, which stops the run and cleans up.

  Where the fields come from: no fixture records a live `GET /api/model` listing. The field names (`type`: `chat`, `decision` or `image`; `capabilities.input`/`output`; `variants[].id`; `cost[]` with an optional `tier`; `enabled`; `status`) come from the 2.2.6 binary's `Model.Info` schema, and the endpoint lists only `chat` models by default. That is why the name exclusions and the two-variant rule apply as well.

  An explicit `--chatgpt-model openai/MODEL[#VARIANT]` works as before, and must still be listed.
- **Tonight's command (run it as this script path):** `python3 -B tests/qualification/opencode_live.py --isolated-dir "$Q" --call-ledger ~/.local/state/vibe-workflow-qualification/calls.jsonl --chatgpt --chatgpt-model auto --chatgpt-timeout 900 --out <path>`, with a fresh, empty, owner-only `$Q` outside any git worktree, and `--out` outside the repository and `$Q`.

  Do not use `python3 -m tests.qualification.opencode_live`: importing the `tests` package as the main module makes `tests/__init__.py` redirect `HOME` to a temporary directory. The real-directory snapshots and the server environment would then use that temporary home. Every live mode therefore refuses to start, with exit 2 and before anything starts, when `HOME` is not the current user's home from the password database.
- **Untested live:** `prepare` for the explicit `openai` choice (harness `opencode provider openai`, pool `chatgpt-sub`) is unit-tested against the real `scripts/routing.py` on a temporary store, but has never run on OpenCode. The unit tests stub the server start and the plugin-chain probes.
- **The user's directories.** The run also watches `~/.codex` (top level only: names and mtimes, never contents) and `~/.config/openai`. A change to `~/.codex/auth.json`, `config.toml` or any top-level file named like auth, token or credential, or any change under `~/.config/openai`, fails the run. Other `~/.codex` changes are the running Codex app's own, so they are counted and reported, not failed. `~/.claude` and `~/.opencode` stay report-only as before.
- **Request (1 call).** One titled session (so no title request) with `Reply with exactly: OK` goes through the full managed handshake. Then `descendants`, accounting (subscription: tokens only, cost null), `record complete`, `detach` and `status`. The run stops after the first successful request. The 3 remaining calls are reserved for retries, title or compaction observations only with the coordinator's go, and the harness does not send them by itself.
- **Pass:** exactly one `primary` request, null costs, settlement `window`, `detach` succeeded, and `status` is clean.
- **Recorded:**
  - whether OpenCode's `native.rateLimit` appears, and its keys;
  - the transmitted request kinds;
  - the title state.
- **Codex window: not read.** The repository has no standalone reader of the Codex `account/rateLimits/read` (`scripts/usage_codex_host.js` is a host bridge that receives limits from the Codex app), so the before and after windows are `unobserved`.
- **Observed:** _(not yet run; waits for the user's interactive sign-in)_

## Live probe run: descendants and s33d (harness 85ae5f4)

**Run:** 9 November 2026, 14:11:29–14:11:53 UTC (24 s). Command: `--stages descendants,s33d` from a fresh worktree at `85ae5f4`, with a fresh owner-only isolated directory. Exit 0, no blockers, no deviations.

**Calls:** 7 transmitted, all `claude-agent/haiku#low`, under `qualification-2`:

| Ledger reservation | Reserved | Sent |
| --- | --- | --- |
| guard probe | 1 | 0 |
| pending probe | 1 | 0 |
| descendants: tool subagent | 3 | 3 |
| descendants: command child | 1 | 1 |
| descendants: API child | 1 | 1 |
| s33d: config and ordinary edits | 3 | 2 |
| s33d: env command (prompt held) | 1 | 0 |

The durable ledger now reads `qualification-2` **7 of 10**, with qualification 9 of 14, chatgpt 0 of 4, pilot 23 of 28, and no open reservations. Raw sanitized evidence: [`qual2-results.json`](../tests/fixtures/smart-routing/probes/qual2-results.json); ledger view: [`qual-call-ledger.json`](../tests/fixtures/smart-routing/probes/qual-call-ledger.json).

**Setup.** OpenCode 2.2.6 and the plugin chain `qual-pre` → `vibe-workflow.routing` → `qual-post` were proven again:
- the guard probe (variant `high`) was blocked by `qual-post` (`guard_variant`), 0 sent; it is the run's one off-route request;
- the pending probe was blocked by the shim (`attach_required`), 0 sent; it is the run's one `pre` record without a `post`;
- `routing.health` was `ok` with `managed_denies` `config_edit` and `image_generation`.

### `descendants`

| Criterion | Expected | Observed | Result |
| --- | --- | --- | --- |
| Positive `descendants` answer | `complete: true`, no `reason` | HTTP 200, `complete: true`, `reason` null, 3 descendants, `truncated: false` | pass |
| Equal to the launcher listing | same sessions and parents | 3 listed, equal | pass |
| Tool subagent | observed, or `unobserved` (not a failure) | **observed**: the model called the subagent tool, which passed `tool.execute.before`; the child ran one request on `haiku#low` | pass |
| Command child (`qualchild`) | present | present, 1 request; the parent's follow-up was held (`guard_hold`, 0 tokens) | pass |
| API child (`parentID`) | present | present, 1 request | pass |
| Accounting with `shim_descendants` | no `shim_*` unknowns | no unknowns | pass |
| `record complete`, `detach` | `window`, detached | `window`, detached | pass |
| `descendants` after `detach` | refused | refused | pass |
| Negative run (root created before `attach`) | `complete: false`, `root_creation_unobserved` | HTTP 200, `complete: false`, `root_creation_unobserved`, 0 calls | pass |
| Negative accounting | `shim_lineage_incomplete` | `no_requests`, `shim_lineage_incomplete` | pass |
| API-route semantics (offline) | all costs null | null; unknown `cost_unknown`, `shim_lineage_incomplete` (claude-agent's cost 0 nulls API costs by itself, so this check cannot separate the two causes) | pass, weak |
| Routing `status` afterwards | clean | clean | pass |

Also recorded:
- **Root creation:** OpenCode emitted the derived root's `session.created` after `attach` (1 event), so the shim saw it while attached.
- **Event stream:** no error.
- **Shim latency (model requests):** median 0.61 ms, max 0.82 ms (n = 10, covering the whole run: setup probes, `descendants` and `s33d`).

### `s33d`

| Criterion | Expected | Observed | Result |
| --- | --- | --- | --- |
| Config edits | at least one attempted config path; every attempted one denied | `.opencode/command/x.md`: 1 attempted, 1 denied; `sub/opencode.json`: 1 attempted, 1 denied. Both used the `write` tool, which passed `tool.execute.before`; the shim's `permission.evaluate` turned `allow` into `deny` | pass |
| Config files absent afterwards | absent | absent | pass |
| Ordinary edit `agents/ok.txt` | attempted, allowed, written | 1 attempted, allowed, written | pass |
| Project changes | only `agents/ok.txt` | `agents/`, `agents/ok.txt` | pass |
| Settlement and `status` | `window`, detached, clean | as expected | pass |

Also recorded:
- **Edit resources:** all three were **relative** paths (`.opencode/command/x.md`, `sub/opencode.json`, `agents/ok.txt`); `inside_project` is `null` for each, because it is computed only for absolute paths. This is consistent with the shim resolving them against `ctx.location.directory` (the session directory), but does not prove which base it used: the paths would also resolve the same against the server's project directory. The `agents/` write was allowed, which matches the rule that `agents/` is protected only inside a config directory.
- **Requests:** the model wrote all three files in one turn and finished in a second turn, so 2 of the 3 reserved calls were used.
- **`` !`env` ``:** the marker was **`present`**: the command's shell ran with the dummy credential-like `QUAL_PROBE_TOKEN` visible. No `shell.create.before` record was logged, by the recorders or the shim. The command template's shell expansion does not pass that hook, so the shim's environment stripping does not apply to it. Only the marker was recorded, never the value.
  - **Positive control.** This run denied the `shell` tool, so it has no positive control of its own. The control is the earlier [stages 0–4 re-run](#re-run-stages-04-harness-3f87159), where a shell-tool shell passed `shell.create.before`, was recorded, and had the same variable stripped by the shim.
  - **What the evidence is.** The missing hook record for the command shell is evidence from this run combined with that control. It is not a second observation of the hook firing. The command's prompt was held (`guard_hold`, 0 tokens).
- **Image generation:** `unobserved`; the isolated server lists no image model (0).
- **Shim latency:** `permission.evaluate` median 0.18 ms, `tool.execute.before` median 0.16 ms (max 0.70 ms). Each is n = 4 and includes the `descendants` subagent sample (one evaluation and one tool call) besides the three `s33d` writes.

### What this run did not show
- An image request being denied: there is no image model.
- A spawn on another model through the subagent tool, which would be blocked: only the allowed route was used.
- MCP, Code Mode and a failed plugin load.
- `descendants` with `truncated: true` or the descendant budget exceeded.
- An API-funded attempt with real costs.
- Absolute edit resources.
- Whether stripping happens on any other shell path in this run: the edit profile denied the shell tool.

### User directories and cleanup
- **Harness cleanup:** verified by the harness (0 processes left, isolated directory removed). Checked again independently after the run:
  - no `opencode serve` or `routing.py` process;
  - the isolated directory is gone;
  - no `.opencode/command/x.md`, `agents/ok.txt` or `sub/opencode.json` under `~/dev`, `~/.config`, `~/.local/share/opencode`, `$TMPDIR` or `/tmp` (same as before the run);
  - both checkouts' working trees are unchanged.
- **OpenCode user directories:** no unexpected change; 1 desktop-churn change in `~/.local/share/opencode`.
- **`~/.codex`:** no credential or config change; 1 churn change, from the running Codex app.
- **`~/.config/openai`:** absent.
- **Report-only:**
  - `~/.claude`: 7 added, 1 changed, 1 removed;
  - `~/.claude.json`: changed;
  - `~/.opencode`: unchanged.

  The claude-agent provider uses the user's Claude login through `HOME`, and Claude Code sessions were running on the machine during the run, so these changes cannot be attributed to either.

## Re-run: stages 0–4 (harness 3f87159)

- **Run.** Qualification mode with `--through 4`. Model `claude-agent/haiku#low` only. Calls went through the durable call ledger under purpose `qualification`; canary mode and the `pilot` purpose were not used.
- **Evidence.** [`qual-rerun-results.json`](../tests/fixtures/smart-routing/probes/qual-rerun-results.json) is the harness's output, committed unchanged. One defect in that output: the redactor's id pattern matched the key `call_ledger` and renamed it `<call#1>`, and the value came out `null`. The durable ledger is recorded in [`qual-call-ledger.json`](../tests/fixtures/smart-routing/probes/qual-call-ledger.json), which is exported from that file. The round-1 `qual-results.json` is kept unchanged, because tests from #146 read it.
- **Cleanup, verified by the harness itself.**
  - 0 processes left; the isolated directory was removed.
  - The user's OpenCode config, data, state and cache directories showed 0 unexpected changes. There were 2 desktop-app churn entries in `~/.local/share/opencode`: the running desktop app's own database and log files.
  - Report-only: `~/.claude` had 5 entries added, 3 changed and 1 removed (Claude CLI session state), and `~/.claude.json` changed.
- **Count basis.** Calls are counted as OpenCode `model.request` hooks, not upstream API calls.

| Stage / check | Expected | Observed | Result |
|---|---|---|---|
| 0 plugins and order | three plugins; `qual-pre` → shim → `qual-post` | All three listed. Guard probe (`haiku#high`, interactive): `qual-pre` saw it, then `qual-post` refused it with `guard_variant`. Pending probe (an admitted, undispatched derived session): `qual-pre` saw it and the shim refused it with `attach_required`, so `qual-post` never saw it; the probe was released with `not_dispatched`. 0 sent. | pass |
| 0 health, `authority_required`, `prepare`, `status` | `not_ready` → `ok`; stamped | `not_ready`/`ledger_absent` (58 ms with the child's start-up), then `ok` (17 ms); `authority_required: true`; admitted `included_subscription`; `unstamped_open_holds: 0` | pass |
| 1a derived ids, one request | accepted, 1 request | ids kept; 1 hook call, 1 sent, outcome succeeded | pass |
| 1b duplicate send | no request, original returned | 0 hook calls, 0 sent, original id returned; one extra `idle` record | pass (as in run 1) |
| 1c / 1d / 1e | read back; own ids not derived-shaped; insertion order | HTTP 200 with the same id; 26-character mixed-case ids; derived ids stayed at positions 1 and 3, listing not lexicographic | pass |
| 2 dispatch, attach, managed request | token once; attached; allowed | `dispatched` (43-character token, never printed), repeat answered `already_dispatched` with no token; a wrong-token attach got HTTP 500, the token attached; 1 primary + 1 title sent, outcome succeeded | pass |
| 2 webfetch, shell, command subagent | denied; stripped; blocked | `ask` → `deny`, listener 0 hits; probe variable present → absent; child on `sonnet` refused with `route_mismatch`, 0 sent, no `tool.execute.before` | pass |
| 2 detach, unattached and wrong-token prompts | refused/detached; blocked at 0 | wrong token HTTP 500, then `detached`; both prompts `attach_required`, 0 sent | pass |
| 2 closed attempt after `record complete` | `attempt_closed`, 0 sent | **`attach_required`**, 0 sent, no assistant message | **deviation** (blocked, wrong reason) |
| 2 interactive session, routing child killed | passes untouched | child killed (1 process); 1 sent, outcome succeeded, shim time 0.15 ms; `health` afterwards `ok`, child `respawned` | pass |
| 3 re-read with `children` + `lineage` (#146) | usage for the attempt | 2 message pages, 1 command child included, 8 lineage entries; `consistency_report`: 0 unaccounted, 0 lineage conflicts, 0 unknown records; `synthetic` counted as its own type | pass |
| 3 title by subtraction | one real title, remainder known | title `known`: input 2, output 4, cache read 823, cache write 346; 1 title sent | pass |
| 3 `usage_from_messages` | subscription usage | source `host_message_totals`; one `primary` and one `title` request, every `cost_usd_micros` null, `unknown: []` | pass |
| 3 `track_events` over the live stream | no gap | 81 session events (76 durable, 5 volatile ignored), 0 normalize failures, 0 duplicates. No `gap`, `out_of_order`, `seq_conflict`, `reset` or `joined_late`; **`version_changed` on all 5 session aggregates** | pass for gaps; finding below |
| 3 `reconcile_with_state` | zero deltas | all token deltas 0; 0 messages added or missing; nothing regressed | pass |
| 3 `record complete {usage}` | window settlement, no dollars | `recorded`, outcome `complete`, settlement `window`, released `win:sub/5h: 12`, `observed_units: {}` (usage carries tokens, not window units), no charge | pass |
| 3 `status` afterwards | clean | no holds, 0 open attempts, `unstamped_open_holds: 0`; nothing `cost_unknown`, uncertain, blocked or awaiting reconciliation | pass |
| 3 `generate` requests | record if any | none | — |
| 4 lost acknowledgment | counted; message absent; `uncertain` keeps exposure | Launcher killed after `dispatch` (exit −9). The hold was `dispatched` and counted, and the derived message and session returned HTTP 404. A session-only check answered `authority_required`. `record uncertain` → hold `uncertain`, still open; a check answered `attempt_uncertain`; `complete` answered `reconciliation_required`. | pass |
| 4 operator recovery for an uncertain subscription hold | none exists | `reconcile_uncertain` → `hold_state_forbidden`. **No operator path exists for an uncertain subscription hold**: it stays open and counted. Here it stayed only in the temporary ledger, which was deleted. | recorded gap |

**Order evidence.** For the guard probe, `qual-pre` recorded the request and then `qual-post` recorded `guard_variant`, after it in time. For the pending probe, only `qual-pre` recorded it. Together these show the hook order `qual-pre` → shim → `qual-post`. Two requests were off the route, and neither was sent: the `haiku#high` guard probe and the `sonnet` command child.

**Latency.** Shim time is measured from the `qual-pre` hook to the `qual-post` hook:

| Hook | n | p50 | p95 |
|---|---|---|---|
| `model.request` primary | 5 | 0.50 ms | 0.63 ms |
| `model.request` title | 1 | 3.35 ms | 3.35 ms |
| `permission.evaluate` | 1 | 0.22 ms | 0.22 ms |
| `shell.create.before` | 6 | 0.13 ms | 0.14 ms |

Routing operations (one-shot `routing.py`, mostly interpreter start-up), each n = 1:

| Operation | Time |
|---|---|
| `prepare` | 81 ms |
| `dispatch` | 76 ms |
| `record complete` | 76 ms |
| `status` | 75 ms |
| `set authority_required` | 78 ms |

RPC round trips: `health` 17 ms warm and 58 ms with the child's start-up; `attach` 1.5 ms (it makes two child checks).

**Deviations and findings**

- **Closed attempt answered `attach_required`.** The request was blocked (0 sent), but with `attach_required` instead of `attempt_closed`. The earlier blocked prompts after `detach` had put the session in the shim's pending cache, which lasts 5 minutes ([contract](../clients/opencode.md#managed-and-interactive-sessions)). Within that window the shim answers from the cache without asking the ledger again, so a finished attempt shows `attach_required` until the entry expires. Nothing is loosened: the session stays blocked, and `attach` re-verifies the token through the ledger.
- **`version_changed` on every live aggregate.** `track_events` reported it on all 5 session aggregates, so every live stream would request a resync. The resync gave zero deltas, so this errs on the safe side. Whether OpenCode's `durable.version` is a version per aggregate or per event type is not established, and the raw events were not kept.
- **Uncertain subscription holds cannot be recovered.** No operator op applies to them (`reconcile_uncertain` covers dollar holds only), so on a production ledger such a hold would keep its concurrency place indefinitely.
- **Output key renamed.** In the harness output the `call_ledger` key was renamed by the redactor (`<call#1>: null`); the durable ledger's export is the record.

**Not covered by this run:**

- MCP and Code Mode calls; subagent spawns through the tool (only the command path ran).
- Real `webfetch` execution (only permission evaluation).
- A plugin that fails to load.
- Server restart, and racing launchers against the host.
- `generate`, compaction and retry requests.
- API-funded holds.
- The canary and pilot.

## Pilot canary, run 1 (aborted)

The bounded pilot aborted at admission 1. The harness's accounting step raised a `TypeError` after the managed request and its title had been sent (2 calls).

**Root cause, found offline.** `Run.__init__` set `self.pool, self.account = (canary.POOL, canary.ACCOUNT)` (`3ae290c`). That instance attribute shadowed the method `Run.account(session)`, so `CanaryEnv.account` → `self.run.account(session)` called the string `"anthropic:pilot-canary"`. The same shadowing would have broken qualification stage 3 (`"anthropic:qual"`); the qualification re-run passed only because it ran at `3f87159`, before that line existed.

- **Fix:** the attribute is renamed `route_account`.
- **Why tests missed it:** they used doubles for the environment and never called the real `Run.account` from the canary path. A new end-to-end test runs the whole driver path against a live-shaped OpenCode double and the real routing command. The pilot tests now live in `tests/test_qualification_canary.py`.
- **Abort recording:** aborts now record a sanitized origin, as type, the harness file, line and function, and the innermost frame's file and function; never a message or locals. They also record per-operation timings even for a partial admission, and bounded payload shapes for aborted admissions.
- **Reset:** the canary state was deleted with `--reset-canary-state`, after confirming no harness process was alive.
- **Pilot cap:** raised from 24 to 28 by pre-registration Edit 4.

**Round-4 review items (no live run):**

- **Settled accounting.** Before `record complete`, in the pilot and in stage 3, a result that may still be catching up is re-read: at most 5 times, 1 s apart. Any decrease of a token class or change of the message set aborts with `accounting_regressed`. Accounting and adapter errors are never retried. A result still unsettled at the limit is recorded with the deviation `title_unaccounted`. Each admission records the request kinds counted, `title.state` and its number of re-reads.
- **Titled sessions.** Admissions 2 to 20 create their session with a title, so the blocked-title path is no longer relied on; the guard still blocks an unexpected title at 0 tokens.
- **Abort details** separate fixed refusal reasons (`refusal: <reason>`) from harness errors (`harness_error: <type>`) and record how far accounting got: the session read's status, the message pages read, the listing's type and count, and the numbers of children and lineage entries.
- **Collision guard.** Unit tests check real harness instances for attributes that shadow methods or properties, the sources for duplicate method definitions and for calls through `self.`/`env.`/`run.` that do not match the target signature, and every harness file for compile errors and undefined names. The test doubles refuse method overrides. The [validation results](smart-routing-validation.md#pilot-canary-run-1-9-november-2026-aborted-at-admission-1) have the per-criterion table.

- **Observed again on a dedicated canary state:** the plugin-chain proof (guard refusal `guard_variant`, pending probe refused by the shim), an attached managed request, `uncertain` recovery after an abort, the kill switch, and harness-verified cleanup. The repository was left byte-identical.
- **Harness gaps found:**
  - The abort records only the exception type, not its location.
  - Latencies are recorded only for completed admissions.

## Pilot canary, attempt 2 (completed)

Harness `94f9e0e`, dedicated canary state, `claude-agent/haiku#low`: 20 of 20 admissions completed with no abort and no deviation.

- **Settlement and status:** 20 window settlements with no charge, and a clean `status` at the end.
- **Calls:** 21 sent (20 requests plus the one title of admission 1); pilot 23 of 28 in total.
- **Accounting:** 0 re-reads and 0 `title_unaccounted`.
- **Probes:** guard (`guard_variant`) and pending (`attach_required` by the shim) were both blocked at 0.
- **Working tree and cleanup:** the working tree was unchanged throughout, and cleanup was verified.
- **Latency:** routing operations through the persistent child had p95 between 1.75 and 2.61 ms.

The per-criterion table is in the [validation results](smart-routing-validation.md#pilot-canary-attempt-2-9-november-2026-20-of-20-admissions-no-abort).

## Accepted deviations and follow-ups (recorded after the re-run)

- **Closed attempt reason (accepted).** A closed attempt's session answers `attach_required` instead of `attempt_closed` while its pending entry lasts; that entry has a 5-minute lifetime by contract. The session stays blocked at 0 tokens.
- **`version_changed` on every live aggregate (accepted, open P0.8 item).** The resync gives zero deltas. Whether `durable.version` is a version per aggregate or per event type is not established.
- **Uncertain subscription holds (follow-up).** They have no operator recovery path. The plan's follow-up task "subscription hold reconcile" is required before cutover and before any production use of enforced routing. Until then the pilot runs on a dedicated canary ledger.
- **Probe exposure.** The probes' reserved calls count against the cap of the purpose they run under (`pilot` in canary mode).

## Settling a dead run's reservation

Every send is written ahead to the durable call ledger, and an unsettled reservation counts in full. If a harness run dies (killed, crashed or interrupted before it settles), its reservation stays open, which errs on the safe side. To close it:

1. Make sure no harness run is alive: `ps -axo pid,command | grep opencode_live.py` shows nothing but your `grep`. Also make sure the run's isolated server is gone; the harness removes it on any exit it can handle.
2. Find the reservation's sequence number: the `seq` of the `reserve` record with no `settle` record whose `ref` names it, in `~/.local/state/vibe-workflow-qualification/calls.jsonl`. Read the file; never edit it.
3. Run `python3 -B tests/qualification/opencode_live.py --call-ledger ~/.local/state/vibe-workflow-qualification/calls.jsonl --settle-dead <seq>`.

   It refuses an unknown or already settled reservation, a reservation whose recorded pid is still alive, and any state in which another harness run is alive. It records the settlement at the full reserved count, so it can never under-count; a dead run is assumed to have sent everything it reserved.
4. Regenerate the committed view with `--export-call-ledger tests/fixtures/smart-routing/probes/qual-call-ledger.json`.

## Pilot harness fixes after the delta review (no live run)

| Item | Change |
|---|---|
| D1, blocking: stuck holds in production | The pilot runs on a dedicated canary routing state. Settings and ledger are pinned to an owner-only directory outside every repository and run directory (default `~/.local/state/vibe-workflow-qualification/canary/`). Production default paths, worktrees, OpenCode's and the production state's directories and the isolated dir are refused. The harness creates the state on first use (one-way `authority_required` on the canary ledger only, Smart Routing ON, API Usage off, no organization) and refuses an unclean one with reset advice. Pilot admissions use their own account and pool ids. |
| D2: catch-all abort | `PilotDriver.run` catches every exception and signal. Recovery depends on how far the admission got (see the pre-registration). A reservation is released at 0 only when nothing could have been sent. The kill switch's own failure is recorded, not raised, and further signals are ignored during recovery. |
| D3: preconditions first | Every refusal decidable without a server runs before the project config, the `.git/info/exclude` edit, the server or the probes. Tests check that a refusal leaves the repository and the exclude file byte-identical and starts no server, and that no admission begins unless the probes pass. |
| D4: guard probe reason | `verify_plugin_chain` requires the guard probe's session to fail with `guard_variant`. A provider-side rejection no longer counts. |
| D5: tool-less sessions and working tree | Static evidence that the `claude-agent` provider in the 2.2.6 binary passes `tools: []`, `settingSources: []`, `disableAllHooks`, `strictMcpConfig`, `permissionMode: "dontAsk"` and only OpenCode's own MCP tools. There is no option to add; the harness's sessions deny every OpenCode tool. Working-tree snapshots at the start, after every 5 admissions and at the end: new dirty paths abort the pilot; already-dirty paths and HEAD moves are reported. Residual risks are in the pre-registration. |
| D6: dead runs | `--settle-dead SEQ`; reservations now record the reserving pid. Malformed ledger records are refused as damaged instead of raising `KeyError`. |
| D8: output defects and tests | The redactor no longer takes field names such as `call_ledger` for ids: an id needs a digit or 20 characters. Added tests for run sequencing, `CanaryEnv`, and the canary state and kill switch against the real `routing.py` on temporary stores. |

## Round-3 review fixes (no live run)

- **Persistent routing child.** `served()` checks every reply id. A timeout, an unreadable or mismatched reply, or an interrupt kills the child, so a late reply can never be read as another request's answer. Only reads (`status`, `check`) are re-sent, to a fresh child; a write (`prepare`, `dispatch`, `record`, `set`) is never re-sent and answers `unconfirmed`.
- **Cleanup order.** Cleanup stops the server, removes the project config and restores `.git/info/exclude` first, each step independent. It reads the hooks afterwards, tolerating a damaged last line (one retry, then `hooks_unreadable`) without failing cleanup.
- **Ancestor configs.** Canary mode refuses any OpenCode config in the project or its ancestors. None exists today on the allowlisted path.
- **Signals.** The abort path ignores signals before any recovery step.
- **Working-tree snapshots** use `GIT_OPTIONAL_LOCKS=0`, so they never rewrite `.git/index`.
- **Guarded directories** are resolved through symlinks.
- **Guard-probe reason.** Its exact text was not observed live through this check. A different reason stops the pilot before any admission, and the two probe reservations settle at 0.

## Run 1 (stopped in stage 3)

- Harness: [`tests/qualification/opencode_live.py`](../tests/qualification/opencode_live.py) acted as the managed launcher. [`opencode_inspect.py`](../tests/qualification/opencode_inspect.py) re-read the stopped run without any model call. Pure parts are tested in `tests/test_qualification_harness.py`. The harness was hardened after an independent review; see [Harness changes after review](#harness-changes-after-review-no-live-run). This report's results come from the earlier version.
- Evidence:
  - [`qual-results.json`](../tests/fixtures/smart-routing/probes/qual-results.json): run 3, plus the re-read. Its `_meta.provenance` lists the one field edited after the run and the hand-written parts.
  - [`qual-call-ledger.json`](../tests/fixtures/smart-routing/probes/qual-call-ledger.json): generated from the durable call ledger.

  Both are sanitized: ids are placeholders, and no paths, names, tokens or passwords are included.
- Model calls: **5 of the 14 authorized**, all `claude-agent/haiku#low`. Four requests were blocked before sending (0 tokens).
- **What the count means:** OpenCode `model.request` hook invocations that passed the shim and the guard. These are not upstream API calls: the claude-agent SDK may retry or split a turn, which would make more upstream calls than counted.

## Setup

- **Server.** An isolated `opencode serve` (OpenCode 2.2.6) on a random localhost port, with separate XDG config, data, state and cache directories under a new `0700` temporary directory. It ran with a minimal environment: `HOME` was kept so the `claude-agent` provider could use the local Claude login, plus a dummy `QUAL_PROBE_TOKEN` variable for the shell check.
  - `claude-agent` was connected through its external `claude-login` method, which stores a method reference only.
  - `serve` takes no `--standalone` flag; a plain `serve` without `--service` already runs a private server. It prints a per-run password, which the harness reads but never outputs.
- **Plugins.** Three, in this order, from the project's own `opencode.json`. Nothing was installed globally.
  1. `qual-pre`: a recorder.
  2. The routing shim, with `python`, `settings` and `ledger` pinned to a temporary routing store.
  3. `qual-post`: a recorder that also guards before sending. In run 3 it blocked any model other than `claude-agent/haiku`, any title except the one allowed, any session on hold, and anything beyond the budget. That version did **not** check the variant, and the counter would have failed open if `qual-post` had not loaded or its log write had failed. Both are fixed now (see below).

  `qual-post` only sees what the shim let through, so its count is the transmitted count. The pre/post pairs show the hooks ran in this configured order.
- **Cleanup.** The harness of run 3 stopped its server; it did not verify cleanup itself. Afterwards the operator removed the temporary directory by hand and checked a process listing (no server or routing child left). The current harness does and verifies this itself.
- **Not separately controlled.** OpenCode's own catalog and update traffic. The harness sets the V1-era `OPENCODE_DISABLE_AUTOUPDATE` and `OPENCODE_DISABLE_MODELS_FETCH` variables, and whether 2.2.6 honors them was not verified. The server also watched `~/.claude/skills`, read-only.

## Isolation under HOME

The server kept the real `HOME` so that `claude-agent` could use the Claude login. These paths outside the isolated directory may therefore be read or written. They were not inspected in run 3.

- **`~/.claude/projects/<encoded working directory>/`** (written): the claude CLI and Agent SDK store session transcripts per working directory. These would include the tiny prompts and replies; the directory names encode the isolated or canary path.
- **`~/.claude.json`** (written): CLI state, such as per-project entries and counters.
- **macOS keychain item of the Claude login** (written): refreshed when the OAuth token is renewed.
- **Under `~/.claude`** (read): `settings.json`, `CLAUDE.md`, `skills/` (the OpenCode server watched `~/.claude/skills`), agents and commands. They may also be written: other CLI state such as `todos/`, `shell-snapshots/` and telemetry.
- **`~/.opencode`** (possibly written): OpenCode's own per-user directory, if this installation uses it.

The current harness snapshots names and mtimes in two places:

- **Compared:** the user's real OpenCode config, data, state and cache directories, before and after a run. Any change fails the run. The running desktop app's own `opencode.db` files and logs are reported as churn, not hidden.
- **Report-only:** `~/.claude` (two levels), `~/.claude.json` and `~/.opencode`. Only counts are recorded.

## Results

Legend: **pass** observed as the contract says; **deviation** observed but differs in a detail; **blocker**; **unobserved** did not run.

| Stage / check | Expected | Observed | Result |
|---|---|---|---|
| 0 plugin load | shim and recorders loaded | `vibe-workflow.routing`, `qual-pre`, `qual-post` in `GET /api/plugin` | pass |
| 0 `routing.health` before a ledger | `not_ready` | `not_ready` / `ledger_absent`, HTTP 200, 70 ms (child start-up) | pass |
| 0 `set authority_required=true` | `updated` | `updated`, `authority_required: true` | pass |
| 0 `prepare` | admitted | admitted, `included_subscription`, pool `sub` | pass |
| 0 `routing.health` after `prepare` | `ok` | `ok`, child `up`, 14 ms | pass |
| 0 `status` | `authority_required`, `unstamped_open_holds: 0` | both; `unbound_open_holds: 1` (no `workflow` binding in this run) | pass |
| 1a derived session and message ids | accepted; exactly one request | same ids stored and returned; 1 hook invocation, 1 transmitted, 1 assistant message, outcome succeeded | pass |
| 1b same prompt, same message id | no second request; original returned | HTTP 200 with the original id; 0 hook invocations, 0 transmitted; but a second `idle` record was appended | pass, deviation |
| 1c read back by derived id | found | HTTP 200, same id, `user` | pass |
| 1d OpenCode's own ids | never `ses_` + 32 lowercase hex | session and message ids: 26-character bodies of digits, lower and upper case | pass (n = 1 session, 2 message ids) |
| 1e ordering with derived ids | not reordered by id | `msg_0…` and `msg_f…` derived ids stayed where inserted among server ids; listing is insertion order, not lexicographic | pass |
| 2 `dispatch` | token once; derived ids | `dispatched`, 43-character token (never printed), derived ids, `reconcile_hint.authoritative: false`; repeat answered `already_dispatched` with no token | pass |
| 2 `routing.health` before launch | `ok` | `ok` | pass |
| 2 `attach` with a wrong token | refused | refused: HTTP 500 `rpc.internal` "RPC call failed" (the shim's `attach_refused` code is not visible to the caller) | pass, deviation |
| 2 `attach` with the token | attached | `attached`, about 1 ms | pass |
| 2 managed request | allowed, completes | 1 primary and 1 title transmitted; finish `stop`, outcome succeeded | pass |
| 2 checks carry attempt, session and token | every check | inferred: `allow` needs `authority: verified` from the command; the wire traffic was not captured | pass (inferred) |
| 2 `webfetch` for the attached session | denied | `permission.evaluate` via the permission API: `ask` before the shim, `deny` after it, response effect `deny`; listener 0 hits (no fetch was attempted, because the evaluation was triggered without a model call) | pass (evaluation only) |
| 2 shell environment while attached | credential-like names stripped | `QUAL_PROBE_TOKEN` present before the shim, absent after it | pass |
| 2 subagent on another model | blocked before sending | `subagent: true` command with `claude-agent/sonnet#low`: child session created directly (no `tool.execute.before`), its request blocked by the shim with `route_mismatch`; 0 transmitted. The child's failure then started a parent turn, which the shim allowed (attached attempt) and the recorder held | pass; see findings |
| 2 `detach` with a wrong token, then the token | refused, then detached | HTTP 500 `rpc.internal`, then `detached` | pass |
| 2 prompt while not attached ("without a token") | blocked, 0 tokens | `attach_required`; 0 transmitted, no assistant message | pass |
| 2 prompt after a wrong-token attach | blocked, 0 tokens | `attach_required`; 0 transmitted | pass |
| 3 re-read, `usage_from_messages` | usage for the attempt | refused: `session not in the attempt` (the command child, linked only by `parentID`) | **blocker** |
| 3 title by subtraction | title remainder known | root only: `title_unknown` (`unknown_record`: the child's `synthetic` result message). Session total 4/12/0/0/5592 (input/output/reasoning/cache read/cache write), the primary message 2/4/0/0/4425; the recorder saw exactly one title transmitted | not confirmed |
| 3 `track_events`, `reconcile_with_state` | no gap; zero deltas | not reached | unobserved |
| 3 `record complete`, settlement, clean `status` | window settlement; clean | not run | unobserved |
| 3 `generate` requests | record if any | none seen | — |
| 2 closed attempt after `record complete` | `attempt_closed` | not run | unobserved |
| 2 interactive session, routing child killed | passes untouched | not run (interactive derived-shape sessions did pass with the child up in stage 1) | unobserved |
| 4 lost acknowledgment drill | hold stays counted; `record uncertain`; `reconcile_uncertain` not applicable to subscription | not run | unobserved |

### Latency

Shim time is the interval between the `qual-pre` and `qual-post` hooks, which includes the routing child's round trip for managed sessions. These are small samples:

| Hook | n | Median | Max |
|---|---|---|---|
| `model.request` primary | 3 | 0.64 ms | 0.76 ms |
| `model.request` title | 1 | 3.13 ms | 3.13 ms |
| `permission.evaluate` | 1 | 0.52 ms | 0.52 ms |
| `shell.create.before` | 6 | 0.29 ms | 0.60 ms |

RPC round trips from the launcher:

- `health`: 2–14 ms warm, 70 ms with the child's start-up.
- `attach`: about 1 ms; it makes two child checks.
- `detach`: about 1 ms.
- One-shot `routing.py` commands: 60–90 ms, mostly interpreter start-up.

## Blocker

**A managed attempt that runs a `subagent: true` command cannot be accounted.** Fixed on `main` by #146 and observed live in the [re-run](#re-run-stages-04-harness-3f87159). That change links children through `parentID`, adds the `lineage` input and normalizes `synthetic` messages, and the harness's stage 3 now passes `children` and `lineage`.

The command creates the child session directly through the server, with `parentID` set to the managed session. No subagent tool call appears in the parent's messages. [`opencode_accounting`](../runtime/opencode_accounting.py) finds children only through subagent tool parts, so it behaves as follows:

- It refuses the attempt when the launcher includes the child, as the wiring's "every spawned descendant session" requires (`session not in the attempt`).
- It does not flag the child when the launcher leaves it out: `child_session_unaccounted` is not raised, so the child's spend would be silently missing.

In this run the child spent nothing, because the shim blocked it. On the admitted route, though, a command child would be allowed (it is a managed descendant) and would spend. That is a P0.8 gap, "usage attributed or marked unknown", and for an API-funded hold an undercount.

The same flow also delivers the child's result to the parent as a `synthetic` message. The adapter does not know that type, so the title remainder becomes unknown, and on an API route every cost becomes `null`. That direction is conservative.

Possible fixes, for the user to choose:

1. Link children by a verified `parentID` (from `session.get`) as well as by tool parts. Flag any session whose parent chain reaches the root but which is not included.
2. Normalize `synthetic` messages as non-model records.
3. Have managed worker profiles deny commands that spawn subagents.

## Other findings

- **Command subagents bypass the spawn hook.** `subagent: true` commands do not pass through `tool.execute.before`, so the shim's spawn check (`subagent_model_required`, the spawn route check) never runs for them. The shim still blocked the child at its first `model.request`, through the lineage it learned from `session.created` (observed). A blocked child's failure starts an extra parent turn on the attempt's route.
- **RPC refusals are opaque to the caller.** OpenCode returns `attach_refused`, `detach_refused` and similar as HTTP 500 `rpc.internal` "RPC call failed". A launcher therefore cannot tell a refusal from a child failure. Both must already be treated as "do not send".
- **A duplicate prompt adds an `idle` record.** Re-sending a prompt with the same message id appended an `idle` (succeeded) record without any request. Accounting ignores `idle`, but a launcher waiting on `idle` should key on its own message.
- **API facts the harness relies on.**
  - Message pages return `cursor: {previous, next}`.
  - `next` stays set on the last page, which comes back empty.
  - `order` together with `cursor` is HTTP 400.
  - Synthetic input with `resume: false` goes to the session inbox, not the history.

  The first two harness runs stopped on the paging behaviour, after one stage-1 call each.

## Harness changes after review (no live run)

An independent review found one blocking safety gap and several should-fix items. All were changed and unit-tested without a server or any model call.

| Finding | Change |
|---|---|
| B1: the counter failed open if `qual-post` was missing or its log write failed | The recorders throw on any failed log write, which blocks the hook. Before anything may be sent, stage 0 requires two things. First, all three plugins must be present. Second, a two-probe proof of order must pass: a guard probe on `haiku#high` that `qual-pre` sees and `qual-post` then refuses with `guard_variant`, and a pending probe on an admitted, undispatched derived session that `qual-pre` sees and the shim refuses with `attach_required` before `qual-post`. The API's plugin list is used for presence only, because its order is not documented. |
| SF1: in-memory budget | A durable call ledger, append-only and flock-protected, outside every run directory, with the file `0600` in a `0700` directory. It has caps per purpose (`qualification` 14, `pilot` 24), writes each call ahead before sending and settles it with what was transmitted; an unsettled reservation counts in full. It was created once, seeded with the 5 spent calls. `qual-call-ledger.json` is exported from it. |
| SF2: variant | The guard refuses any variant other than `low` on every session, and any spawn other than `claude-agent/haiku#low`. |
| SF3: what is counted | Stated here, in the ledger and in every output's `_meta`. |
| SF4: isolation | The harness refuses an isolated directory inside a git worktree or under `~/Library/Application Support`, and refuses unless `opencode --version` is 2.2.6. It snapshots and compares the user's directories as described [above](#isolation-under-home). |
| SF5: cleanup | Every exception is caught and recorded. SIGTERM and SIGINT stop the run, and further signals are ignored during cleanup. The harness kills and re-checks every process in the server's group or naming the run's paths, removes the isolated directory unless `--keep`, removes the canary project config, and records whether all of it was verified. |
| SF6, SF7 | Fixture provenance recorded; cleanup claim corrected. |
| Nits | `--model` removed; the `--keep` message names what the directory holds; the inspection script refuses anything but a stopped, kept run with the three sessions it reads. |

The harness also gained a canary mode for the bounded pilot. Its pre-registration is in [the validation file](smart-routing-validation.md#pilot-canary-bounded-not-the-pre-registered-canary).

## Open questions

- Whether OpenCode 2.2.6 makes catalog or update requests at start-up despite the environment flags.
- Whether `generate` requests create messages: none occurred.
- Whether real `webfetch` tool execution, rather than permission evaluation alone, is blocked for a managed session: it needs a model call to trigger.
- `reconcile_uncertain` applies only to dollar holds. An uncertain subscription hold has no operator path in the command and keeps its concurrency place. This comes from the contract text; stage 4 did not observe it.

## What remains

1. Done: the [re-run](#re-run-stages-04-harness-3f87159) used 4 calls; 5 of the 14 qualification calls remain.
2. Run a canary on a real project (S3.7) with `workflow`-bound preparation (`unbound_open_holds: 0`) and `--project`.
3. Install the plugin globally (decision 3 in the matrix), which this run deliberately did not do.
4. Run the Codex, OpenAI and ChatGPT live checks (matrix "Live checks still open"); none was made here.
