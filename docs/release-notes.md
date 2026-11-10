# Release notes

## 0.1.37

- **Smaller logo.** `assets/logo.png` is now 512 × 512 (259 KB, from 867 KB); the image is otherwise unchanged. The package export stays within Codex's 8 MiB bundle scan with more room.
- **Workspace readiness on OpenCode.** `workflow.py workspace status` reads plugin names from OpenCode's configuration, so local-path plugins such as Caveman count, and reads MCP servers stored under `mcp.servers`. Both previously showed as missing.
- **Smart Routing.** `scripts/routing.py` adds the one-shot operator op `seed_window` to record a subscription window's first reading. OpenCode accounting accepts `vendor/model` ids; per-launch provider options (endpoint pin, output cap, step count) are validated and applied to the launched server; recovery releases OpenRouter-worker runs that were prepared but never executed. API-funded OpenCode and OpenRouter-worker routes stay disabled, and routing is still not cut over.
- The replay pre-registration in `docs/smart-routing-validation.md` now samples the available history of past sessions.
- Package validation does not establish client execution or live API qualification.

## 0.1.36

- **Workstreams uses native children only.** Delegated workers and reviewers run as native child sessions that one coordinator starts; a chat you ask for is an independent conversation. Only the coordinator's thread gets a managed title, `￻<meter> [STATUS · ]<topic>`: the `[W…]` family code, `P`/`C` roles and the shared family register are retired, and children get no managed title. `workstream_context_id` is rejected; children are named `worker_<attempt>` or `review_<attempt>`. Earlier titles and saved title records are left as they are and no longer synced.
- **Smart Routing managed projects.** Adds `scripts/routing.py prepare` wiring for native workers, reviewers, OpenRouter proposals and OpenRouter workers, plus an OpenCode launcher. Activation stays blocked because no production route is qualified yet. API-funded OpenCode and OpenRouter-worker launches stay disabled. Codex selection is advisory; OpenCode-coordinated projects also need Codex enrollment. Claude Code native delegation is held in managed projects. API Usage refuses managed projects before spending. Unmanaged selection is unchanged, but unreadable routing settings block legacy spending. Routing is still not cut over.
- **Usage ledger and recovery.** The ledger upgrades to version 5 on its next write, keeping runs, holds, handoffs and debits. Earlier releases cannot read the upgraded ledger. `scripts/routing.py` adds `drop_debit` for stuck subscription debits and `settle_legacy_call` for unfinished legacy calls; routing operator actions are one-shot commands.
- **Strategy-table rollback.** `scripts/routing_table.py` keeps the previous verified table on refresh, and `rollback` swaps the two.
- **Workspace readiness.** Codex and Claude Code startup record private hook-delivery evidence for `workflow.py workspace status`. Enrolled startup warns when the last readiness record shows missing required items. `validate.py --release` rejects uncommitted shipped changes and changes to shipped content under an existing release tag.
- **Graphify refresh after merges** for projects that enabled Graphify: code-only extraction with no model calls, reported separately from the merge.
- **Startup diagnostics.** Observation deadlines report `observation_deadline`, and runtime lock contention reports a distinct busy error. Unrelated sibling worktrees no longer change a development checkout's qualification fingerprint; installed-version checks remain.
- Package validation does not establish client execution or live API qualification.

## 0.1.35

- **Escalation ladder.** A stuck repair escalates to a more capable worker, through a stronger model or higher reasoning effort, up to five levels within existing limits. The agent asks the user when escalation cannot continue or does not converge, with the attempts, remaining options, a recommendation and the decision needed.
- **Activation is the only approval for an integration.** An active API Usage or OpenRouter integration may be used autonomously within its saved scope, funding and caps; a connected account alone grants nothing, and other paid or local routes still need approval. API Usage's Manual mode is retired: saved Manual setups run as active, and each task is sent live at most once per checkout. Smart Routing control-plane endpoints need known terms that allow confidential data.
- **PR lifecycle.** Agents open every PR as a `[BLOCKED]` draft, set `[READY]` and mark it ready for review once checks and review pass, return it to `[BLOCKED]` when its evidence goes stale, and merge only with recorded permission or the user's explicit request; otherwise the user merges the `[READY]` PR. People never edit the prefix. A PR replaced by other work is retitled `[SUPERSEDED]` and closed with its branch kept. Onboarding's merge question describes this.
- **Seven thread statuses.** `READY` and `BLOCKED` are retired: after triage, work moves to `WORKING` (or `REVIEW` for review work), and replies, decisions and interventions the user owes use `WAITING` with the label shown; a request for the user to review a result uses `REVIEW`. Updates naming a retired status are refused; records and titles written before this release stay readable.
- **Workspace readiness.** `workflow.py workspace status` reports, read-only, whether Codex, Claude Code and OpenCode have the required tools; `prepare-my-workspace` has a verify mode that writes nothing and a prepare mode with numbered steps. Onboarding checks readiness first and hands missing required items to preparation.
- **Project feature settings.** `workflow.py features status|enable|disable|set|unset|migrate|undo` manages per-project choices in `.vibe-workflow/settings.json`, starting with Workstreams. Existing `AGENTS.md` Workstreams choices remain effective; to move them, run `features migrate plan`, then `features migrate apply`, with the project's `--cwd` (a private backup is kept).
- **Planning and handoff.** Task planning resolves where edits happen before the first edit, and a direct commit or push to a merge target counts as a merge. Templates for worker briefs, task records, review reports, pull requests and setup records, plus project-document shapes under `templates/project/` (product, overview, shared and Claude instructions, architecture, design, local preparation, quality) that onboarding links when adapting a project's documents; the policies keep the rules.
- **OpenCode** agents can title their own session (`scripts/opencode_title.py --proof`).
- **Smart Routing** gains reconciliation of uncertain subscription holds, a persisted strategy table from the maintenance refresh, image-generation and config-edit denial for managed sessions, and further qualification diagnostics. It is still not cut over.
- **Packaging and maintenance.** One definition of package membership; a checkout is scanned as its export. `scripts/prune_state.py` reports and conservatively prunes stale transaction locks (dry run by default). The suite runs each shard with a private `HOME`.
- **Re-onboard projects linking into a version-specific cache folder.** As with 0.1.34, links written into `.../vibe-workflow/0.1.34/...` read the old version after upgrading.
- The displayed product name is now **Vibe Workflow**. Package and marketplace identifiers remain `vibe-workflow` and `vibe-workflow-local`, so nothing needs migrating.
- Package validation does not establish client execution or live API qualification.

## 0.1.34

- **Smaller installs.** Releases on the Codex Git marketplace now hold marketplace files at the root and the plugin under `plugins/vibe-workflow/`, so an install no longer copies the clone's `.git` or `tests/`: about 6.4 MB instead of 17 MB. Run `codex plugin marketplace upgrade vibe-workflow-local` and approve the changed hooks; Codex refreshes the installed copy in place.
- For local testing, `python3 -B scripts/package.py` exports a validated package of tracked files, without `tests/`, to `~/.cache/vibe-workflow/dev-package`. Install from that folder, not from a checkout: clients copied ignored files and Git history from a checkout. If you added a checkout as a marketplace, remove that marketplace and add the export instead (see the README's Development section). `--prune-caches` removes cached copies no client uses. Releases follow [Publish a release](release.md).
- `prepare-my-workspace` can report plugin version drift between Codex and Claude Code and upgrade both clients; after `upgrade record` saves the expected version, startup adds a one-line warning when an installed client's version differs from it.
- A Workstreams thread routes `DONE` through the PR merge gate. Project policy loading and onboarding policy discovery are more explicit; onboarding now writes portable package pointers instead of version-specific cache paths.
- **Re-onboard projects that link into a cache folder.** Instructions written by earlier onboarding can link to `~/.codex/plugins/cache/vibe-workflow-local/vibe-workflow/0.1.33/...`. After upgrading, Codex installs under `0.1.34/`, so those links read the old version or break once it is removed. Run `onboard-my-project` again for those projects.
- OpenCode sessions get Thread Naming titles for ordinary native work.
- Smart Routing for OpenCode subscriptions gains settings, profiles and a deterministic ranker, admission records, a single routing command, the OpenCode adapter and pre-transmission shim, single-use dispatch authority with fresh gates, lifecycle settlement, and usage accounting with reconcile and recovery. It is not cut over: the current selectors keep dispatching until the documented replay and canary criteria pass.
- Tests can run in parallel shards with `python3 -B scripts/test_sharded.py`; oversized test files were split and wall-clock dependence removed.
- Package validation does not establish client execution or live API qualification.

## 0.1.33

- A Workstreams thread is `DONE` only after every PR it opened or took over is observed merged on the remote; until then it shows `WAITING`. Threads whose scope excludes publication need no extra step, and other editable threads record an explicit no-PR decision. Runtime verification still enforces required publications only.
- Add **Review my Project** to review selected repositories against their own project rules. It runs only local checks it can confirm are safe, returns findings in chat, and applies no fixes or installs without separate approval.
- Ask about Graphify and the memory vault during project onboarding, defaulting to yes.
- Smart Routing's usage ledger now allows parallel managed API runs, with an allowance policy, padded claims and concurrency caps (default 4 total, 2 per account). This replaces the one-run-at-a-time limit described in 0.1.32. Its simulation and shadow modes are removed.
- **Breaking rename.** The plugin and local marketplace are now `vibe-workflow` and `vibe-workflow-local` (previously `vibe-coding-workflow`), and the repository is `fcoouto/vibe-workflow`. Project enrollment moves to `.vibe-workflow/project.json`, machine settings to `~/.vibe-workflow/machine.json`, the Workstreams family register to `~/.vibe-workflow/workstreams-families.sqlite3`, and usage state to `~/.local/state/vibe-workflow/usage`. Nothing is migrated automatically: reinstall under the new name and re-run onboarding, or move existing state by hand. Old directories are left untouched.
- To carry 0.1.32 state over by hand, create `~/.vibe-workflow` first, then copy rather than move: `sqlite3 <old> ".backup '<new>'"` for the Workstreams family register and `cp -Rp` for the usage directory. The usage ledger keeps its open runs and upgrades itself on first write. Per-checkout routing activation is keyed to the enrollment file's path and content, so activate routing again for each checkout. If status reports `reuse_available`, the approved setup can be reused; otherwise set it up again.
- `machine.json` cannot be copied because it records the old directory chain; prepare managed review storage again if you use it. Rename the `vibe-coding-workflow:` markers in project `AGENTS.md` Workstreams blocks, or that choice reads as not configured. Keychain service names keep their `vibe-coding-workflow.` prefix.
- Add the Claude Code local marketplace manifest.
- Package validation does not establish client execution or live API qualification.

## 0.1.32

- Present the existing credit and routing management skill as **Manage API Usage**, preserving its command and reference paths.
- Reuse approved API connection and funding setup when explicitly activating another enrolled checkout. Keep project permissions, qualification and consent separate, without adding a mandatory spending cap or smoke test.
- Preserve other projects' activation when the shared funding policy is unchanged. Reject stale setup selections and conflicting or missing ledger pins before reuse.
- Explain profile and stored-connection differences, and resolve equivalent setups using the requested target mode. The shared limit remains one managed API run at a time across the local ledger.
- Include the native workflow evaluation contract and fixtures already merged on `main`; package validation does not establish client execution or live API qualification.

## 0.1.31

- Consider eligible repository coding tasks for approved OpenAI API and OpenRouter routes while preserving local-model priority.
- Keep transmitted source within its approved scope and require native checks and independent review before acceptance.

## 0.1.29

- Define shared checkpoint handoffs and coordinator follow-through for ordinary and managed child work. Keep the parent responsible for collecting results, continuing ready work and reporting user-actionable blockers.
- Preserve messaging permissions, ownership and continuation limits. A status snapshot does not complete supervision or establish automatic background wakeup.

## 0.1.28

- For new threads, keep associated deliverable chats and nested workers in the root coordinator's display family. Pass that binding before creation.
- Store and display child role `C`; use native thread IDs for identity. Leave existing threads and their records unchanged.

## 0.1.27

- Apply the shared assignment role preparation to every workflow subagent, including reviewers, evaluators, nested children and native supervisors of external routes. Reuse existing external brief and proposal fields.
- Prepare managed continuations once before consuming the send intent. Preserve the original prompt and continuation controls when display metadata is unavailable.
- Reuse saved roles and exact correlation markers on native assignment retries. Keep missing or conflicting display metadata nonblocking.

## 0.1.26

- Assign complete outcomes to reusable native children through tests, guidance and review fixes. Keep coordinator decisions and integration distinct.
- Route ordinary delivery and handoff to shared ownership rules. Preserve small direct tasks, explicit delegation with Workstreams disabled, and separate advanced controls.

## 0.1.25

- Preserve declared subagent badges when a coordinator moves to a worktree and its native child retains the original checkout. Validate both native checkouts independently for display while retaining exact parent, child and task identity checks.
- Resolve global and checkout-scoped Habblaud start hooks against the verified parent and child checkouts. Saved declarations keep their original assignment checkout for recovery.

## 0.1.24

- Manage one Workstreams Enabled/Disabled choice during onboarding or through the Workstreams skill. Both read and update the same project instruction checkbox.
- Enable suitable automatic native coordination without a runtime database or separate activation step. Preserve small direct tasks, explicit delegation and required independent review.
- Keep resource admission and capacity recovery as separate advanced controls. Preserve existing choices, naming, ownership and protections.

## 0.1.23

- Support cooperative completion for bounded managed native workers and reviewers. Reconcile known work so checks and review can proceed, and keep uncertain ownership reserved.
- Recognize exact Codex `functions.exec` records for the guarded patch and foreground wait. Keep repository merge gates and strict recovery proof separate.
- Bind the guarded patch to the native child. The standalone Mac CLI pilot passed with one guarded edit, a whole-file check and a tool-free Astra review of the same candidate.
- Use the existing D4 journal limit when rereading retained recovery evidence.
- Avoid repeating the D4 recovery source scan before the final commit check.
- Recognize macOS's system configuration path during Codex setup verification.

## 0.1.22

- Reopen the Thread Naming meter before substantive work resumes. Keep accepted milestones that still apply to the current objective.
- Switch automatically to a clear new objective in the same chat. Keep the family and member, reset progress, and rename only a workflow-owned topic.
- Preserve the full meter for questions about completed work and acknowledgments. Keep user-edited topics and title synchronization pauses. [PR #94](https://github.com/fcoouto/vibe-coding-workflow/pull/94)

## 0.1.20

- Restore saved Habblaud badges after a receiver restart. The bundled upgrade patch preserves the receiver's existing data and supports restoring previously observed declarations.
- Reuse the same badge declaration when preparing a follow-up for an existing native subagent. Capture a missing role once and keep the follow-up available if badge delivery fails.
- Keep Habblaud delivery and recovery in one adapter shared by native delegation, OpenRouter and Usage Routing.

## 0.1.19

- Make cumulative and per-task Usage Routing caps optional. Verified funding, applicable monthly limits, and full per-request reservations still apply. [PR #91](https://github.com/fcoouto/vibe-coding-workflow/pull/91)

## 0.1.18

- Capture each subagent's role once while preparing its assignment. Reuse existing native, OpenRouter and Usage Routing metadata without blocking execution when display preparation fails. [PR #90](https://github.com/fcoouto/vibe-coding-workflow/pull/90)
- Bundle the Habblaud receiver fix for onboarding and upgrades: preserve nested child identity and immediate parents, retain early activity, and keep the original fallback when no role is available.
- Verified first observed badges in a fresh native check: `Coordinator · GPT-6-Astra` and nested `Reviewer · GPT-6-Astra`. No new external-provider or Claude Code live execution was performed. Native cache activation remains a separate client refresh; preserve active chats using older package paths.

## 0.1.17

- Enable live Manual and Smart Balance after the user connects an OpenAI project key and activates a regular routing policy. Comparisons and smoke tests are optional.
- Isolate smoke-test budgets from ordinary routing. Require explicit regular-budget setup and preserve prior charges and reservations. [PR #89](https://github.com/fcoouto/vibe-coding-workflow/pull/89)

## 0.1.15

- Enable Usage Routing even when other Codex work is active. Account for unrelated activity when attributing pilot measurements. Live admission still requires its existing qualification and safeguards. [PR #88](https://github.com/fcoouto/vibe-coding-workflow/pull/88)

## 0.1.14

- Include the delegation configuration and visibility changes from [PR #87](https://github.com/fcoouto/vibe-coding-workflow/pull/87) in the Git-backed marketplace release.

## 0.1.13

- Publish the Codex marketplace from a Git release branch so Codex can upgrade it while preserving the existing marketplace and plugin IDs.

- Fix onboarding inventory to fingerprint large, unchanged assets without loading whole files into memory. Preserve source and action body limits, aggregate budgets, secret exclusions, file identity and change detection. Add focused regression coverage for large assets, changed inputs and budget exhaustion.
