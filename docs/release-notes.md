# Release notes

Historical entries retain their original terminology and observed titles; current
workstream-session and subagent distinctions are in [Agent Delegation](../policies/03-agent-delegation.md#outcome-ownership).

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
