# Workspace readiness and onboarding — improvement plan

Date: 2026-11-09. Status: **draft for user review.** Supersedes the narrower [workspace client coverage plan](2026-11-09-workspace-client-coverage.md) (its parts A and C are folded in below).

## 1. What goes wrong today

Observed in this repository's own onboarding (2026-11-08/09):

| Failure | Where it came from |
|---|---|
| Superpowers, Ponytail, Context7, RTK and the Graphify skill were never set up for OpenCode, the client the user works in. | `prepare-my-workspace` follows a Toolkit prompt that names only Codex and Claude Code; nothing discovers which clients are installed. |
| Onboarding went ahead on a workspace that was not ready, without saying so. | `onboard-my-project` treats machine tools as someone else's job and only "reuses" them. No readiness check exists. |
| Graphify was skipped as "pending, separate user choice". | Optional ≠ omittable was never enforced; the plan validator accepts `omit` for an applicable component. |
| Codex silently ran a stale plugin copy; trust silently lapsed after an upgrade. | Install, trust and startup were reported as one fuzzy state; the version check could not see a same-version change. |
| The agent skipped Thread Naming, role declarations and worker selection. | Procedures depend on the agent remembering to load them. |

The Toolkit already states the rule this plan enforces: *"Mandatory plugins are not optional extras and must not be skipped because they are not yet on the machine."* Its environment prompt also says the request itself authorizes the required foundation.

## 2. Principles

1. **One definition of "ready".** A single requirements catalog says what the workspace needs, why each item matters, how to check it per client, and whether it is mandatory. Both skills read it; neither keeps its own list.
2. **Check with code, act with the agent.** A read-only readiness command produces the same answer every time. The agent then fixes what it reports, following each tool's official instructions.
3. **Every installed client counts.** The client running the session and every other installed agent client (Codex, Claude Code, OpenCode) are covered, or reported as an explicit gap.
4. **Mandatory means presented as a requirement, with its value.** The user sees what is missing and why the harness needs it, then the agent proceeds. Only actions that need the user (sign-in, trust approval, system password, purchase, authentication-mode change) wait for them.
5. **Separate procedures, one gate.** Preparation and onboarding remain distinct skills. Onboarding calls the readiness gate first and hands off to preparation when needed, then resumes.
6. **No silent states.** Every requirement and every catalog component ends in a named state. "Not seen" stays pending; nothing is reported as working before a new session shows it.

## 3. Requirements catalog (new, single owner)

File: `clients/workspace-requirements.json` with a plain-language companion `clients/workspace-requirements.md`.

Each entry: `id`, `name`, `why` (one sentence of value shown to the user), `tier`, `scope` (`machine` or `per-client`), `check` (how the readiness command observes it per client), `install_ref` (where the agent reads the official instructions; no hard-coded commands as truth), `user_actions` (sign-in, trust…).

| Tier | Items | Gate behavior |
|---|---|---|
| **Harness (mandatory, gates onboarding)** | Python 3.11+, Git, GitHub CLI; the `vibe-workflow` plugin at the expected version with hooks trusted (in every client it supports); Superpowers, Ponytail, Caveman, RTK, Context7, Graphify in every installed agent client that officially supports them | Onboarding cannot finish while any is missing; it prepares them first. |
| **Foundation (mandatory, machine)** | Node.js LTS, npm, pnpm, ripgrep/fd/jq, Docker + Compose, package manager | Always prepared by `prepare-my-workspace`, as the Toolkit requires. Gates onboarding only when the project or a selected workflow needs it (e.g. a Node project without Node). Always reported, never silent. |
| **Optional extras** | Toolkit extras, browser tools, Sentry… | Never gate; offered with their value when a project needs them. |

A tool with no official support for a client is `not supported (reason)`, never improvised.

**Docker is foundation, not harness (user decision, 2026-11-10).** The Toolkit lists Docker and Compose as mandatory machine foundation, but none of its workflows, prompts, tool guides or templates use it; only the optional Harness2 benchmark lab does. `vibe-workflow` itself needs only Python, Git and the agent plugins. Docker is therefore never an onboarding blocker: preparation installs it, and onboarding requires it only when the project or a selected workflow uses containers.

## 4. Readiness command (new code, read-only)

`python3 -B scripts/workflow.py workspace status [--json]`

- Discovers installed agent clients (CLI on PATH, version, config directory) plus the one running the session.
- For each catalog item × client, records one state: `ready`, `missing`, `outdated`, `needs-user` (sign-in, trust), `not-supported`, `unknown` (with reason).
- Reuses existing observation: `runtime/package_versions.py` (plugin versions), each client's own listing commands (`plugin list --json`, `mcp list`), PATH probes. Bounded time, no writes, no network beyond the clients' own local listings.
- Writes nothing. A separate `workspace record` stores the last verified result privately (like `upgrade record`) so startup can warn cheaply.
- Exit code: 0 ready, 1 not ready, 2 could not determine.

## 5. `prepare-my-workspace`, step by step

Each step names what the agent does, when it is done, and what to do on failure. The entry point stays ≤150 words and links this procedure.

| # | Step | Agent does | Done when | If it fails |
|---|---|---|---|---|
| 1 | Discover | Run `workspace status`. Identify OS, shell, installed agent clients, the session's client. | Status table produced. | `unknown` cells stay listed; continue. |
| 2 | Tell the user | Show missing items grouped by tier, each with its `why`, and the user actions that will be needed. Say the agent is proceeding with the mandatory ones. | Message shown. | — |
| 3 | Back up | Back up every client config the run will touch, outside the project. | Backup paths recorded. | Stop that client's changes. |
| 4 | Install / update | In dependency order (prerequisites → Git/gh → runtimes → agent CLIs → mandatory plugins per client → foundation → updates), using each tool's official instructions for that client, read now; record source and date. | Each item's install command succeeded. | Mark `failed` with the error; continue with the rest; no improvised workaround. |
| 5 | Collect user actions | One consolidated list: sign-ins, trust approvals, restarts, OS prompts. | User confirms each, or it stays `needs-user`. | Stays pending; never approved on the user's behalf. |
| 6 | Verify | Rerun `workspace status`; for each client start a new session (or the client's no-model init mode) and confirm discovery/loading. | Every mandatory cell `ready`, `not-supported` with reason, or `needs-user`. | Report the exact cell; no "done". |
| 7 | Record and report | `workspace record` and `upgrade record`; report the client × requirement table and remaining user actions. | Record written; report shown. | — |

Upgrades follow the existing [upgrade procedure](../../../skills/prepare-my-workspace/references/upgrade.md) as part of step 4.

## 6. `onboard-my-project`, step by step

| # | Step | Agent does | Done when | If it fails |
|---|---|---|---|---|
| 0 | **Workspace gate** | Run `workspace status`. If any harness item is missing, say so plainly ("Your workspace is missing X, Y. They give …. I'll prepare them first, then continue onboarding."), run `prepare-my-workspace` scoped to the missing items, then resume. | Harness tier `ready`, or each remaining cell is `needs-user`/`not-supported` and listed. | Items waiting on the user are carried into the final report; independent onboarding continues. |
| 1 | Target and clients | Confirm the checkout; name the session's client and every installed client; for clients the plugin cannot enroll, report the gap (what is unavailable). | Target and client list recorded. | Ask only if the target is ambiguous. |
| 2 | Inspect | Instructions, configuration, commands, baseline checks, brief source-size screening. | Facts and assumptions separated. | Unknowns stay explicit. |
| 3 | Preferences | Ask the standing questions one at a time (publish, merge, Workstreams, Graphify, memory). | Answers recorded, or defaults applied as written. | — |
| 4 | Catalog and plan | Assess every Toolkit component; each ends **planned**, **reused with evidence**, **inapplicable with reason**, or **blocked with prerequisite and resume condition**. Never `omit` an applicable one. | Validator accepts the plan with no silent omission. | Validator names the row. |
| 5 | Apply | Recoverable file changes; startup membership per supported client. | Changes applied and read back. | Rollback path recorded. |
| 6 | Verify | Project checks; policy discovery; startup per client in a new session; Graphify query; memory pointer readable. | Each claim has evidence or stays pending. | Report the gap. |
| 7 | Report | Four states per client (installed, trusted, startup observed, version); changes; user actions; undo location. | Report shown and setup record updated. | — |

## 7. Enforcement (what code checks, not just text)

| Check | Owner | Effect |
|---|---|---|
| Readiness command | `scripts/workflow.py`, new `runtime/workspace.py` | Same answer every run; both skills start from it. |
| Plan validator: no `omit` for an applicable component; `blocked` needs prerequisite + resume condition; unresolved harness requirements are listed as blockers | `runtime/onboarding.py` `_validate_proposal` | The Graphify-style skip cannot pass. |
| Startup readiness warning: an enrolled client whose last workspace record shows missing harness items warns once, like the drift warning | startup path, reads the record only | A user who skips preparation is told at the next session. |
| Version bump per release; `validate.py` refuses a release whose content changed without one | `scripts/validate.py` / release step | Same-version stale copies become visible. |

## 8. Slices

| Slice | Delivers | Depends on |
|---|---|---|
| R1 | Requirements catalog + readiness command (`workspace status/record`) with tests | — |
| R2 | `prepare-my-workspace` procedure rewrite (steps 1–7, all installed clients; folds in the client-coverage draft) | R1 |
| R3 | `onboard-my-project` gate (step 0), client naming, step table; plan validator "no silent omission" | R1 |
| R4 | Startup readiness warning; release version-bump check | R1 |
| R5 | Behavior qualification on this machine: run onboarding from OpenCode on an unprepared client, observe gate → preparation → resume → report | R2, R3 |

Each slice: short design note, implementer worker, independent review, affected tests via `scripts/test_sharded.py` (full run only where shared core changes: R1 and R4 touch `scripts/workflow.py`/startup, so they get one full run each), then merge.

## 9. Decisions needed from the user

1. **Consent for mandatory items.** Proposed: an onboarding or preparation request authorizes installing and configuring harness- and foundation-tier items, announced before acting; the agent still stops for purchases, licensing (e.g. Docker Desktop), authentication-mode changes, sign-ins and trust approvals. Optional extras need a choice.
2. **Gate scope.** **Decided 2026-11-10:** only the harness tier blocks onboarding; foundation items (Docker included) gate only when the project or a selected workflow needs them, and are always reported.
3. **OpenCode as a full plugin client (part B).** Until decided, the readiness report lists "vibe-workflow startup/enrollment: not available in OpenCode" as a known gap.
4. **AGENTS.md wording.** "Keep environment preparation, project enrollment, and routine development distinct" stays true for procedures; add that onboarding must check workspace readiness first and hand off to preparation.

## 10. Acceptance

- Starting `onboard-my-project` on a machine missing a mandatory plugin in any installed client produces the gate message (with the item's value), prepares it, and resumes onboarding without a second request.
- No client × harness requirement is ever left unreported; unsupported cases carry a reason.
- A plan with an applicable component marked `omit` is rejected.
- After an upgrade, a client with lapsed trust or a stale copy is reported, never "ready".
- Package, unit and instruction checks pass; observed client results are reported separately.
