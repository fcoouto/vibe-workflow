# Workspace readiness and onboarding — improvement plan

Date: 2026-11-09, revised 2026-11-10 after independent review. Status: **approved direction; R1 in progress.** Supersedes the narrower [workspace client coverage plan](2026-11-09-workspace-client-coverage.md).

## 1. Problems observed

- Superpowers, Ponytail, Context7, RTK and the Graphify skill were never set up for OpenCode, the client in daily use: nothing asks which clients are installed.
- Onboarding ran on an unprepared workspace without saying so: it only "reuses" machine tools.
- Graphify was skipped as "pending": the plan validator accepts `omit` for an applicable non-mandatory component.
- Codex ran a stale plugin copy and its hook trust lapsed silently after an upgrade.
- Preparation executes the Toolkit's entire environment prompt, so it installs and checks things `vibe-workflow` never uses (Docker, Node toolchains), and the instructions grow with it.

## 2. Principles

1. **One requirements list, owned here.** It names what the workflow needs and why; both skills read it. The Toolkit stays the authoring authority: the list is written and refreshed from it, not executed at run time.
2. **Code checks, the agent acts.** A read-only command reports readiness the same way every time; the agent fixes what it reports with each tool's official instructions.
3. **Every installed client counts:** Codex, Claude Code and OpenCode, plus the client running the session.
4. **Required means required.** The user is told what is missing and the value it brings, then the agent proceeds. Sign-ins, trust approvals, system passwords, purchases, accounts and authentication changes wait for the user.
5. **Hand off, don't stall.** When onboarding finds a missing requirement, it announces and runs preparation as its own operation (own records and backups), then resumes. A missing item blocks only the work that depends on it (for example that client's startup verification); inspection, documents and preferences continue.
6. **Nothing silent, nothing assumed.** Every requirement and catalog component ends in a named state; a cell is `ready` only with the observation it needs (startup needs a new session).
7. **Less text.** Each rule once; numbered steps with a done condition; anything the workflow does not use is removed.

## 3. Requirements list

Source of truth: `clients/workspace-requirements.json`; `clients/workspace-requirements.md` is its human view, kept consistent by a test. Each item: name, why (one sentence shown to the user), scope (machine or each client), per-client check, where the official instructions live, user actions.

| Item | Scope | Why |
|---|---|---|
| Python 3.11+, Git, GitHub CLI | machine | Run the plugin's hooks and checks; publish task branches and PRs. |
| `vibe-workflow` plugin: expected version, hooks trusted | each client | Loads the workflow rules at session start; drift or lapsed trust leaves a client on old rules. |
| Superpowers | each client | Design, planning, execution and review skills the workflow relies on. |
| Ponytail | each client | Smallest complete change by default. |
| Caveman | each client | Concise communication without losing technical substance. |
| RTK | each client | Compact command output, fewer wasted tokens. |
| Context7 | each client | Current library and tool documentation instead of stale memory. |
| Graphify CLI and client skill | machine + each client | Lets agents build and query a local code map. The per-project graph stays onboarding question 4. |

A tool with no official support for a client is `not supported` with the reason, never installed by improvisation. Agent clients themselves are discovered, not required.

**Removed (local deviation, user decision 2026-11-10):** Docker and Docker Compose; Node.js LTS, npm and pnpm; the Node version manager; ripgrep, fd and jq; the system package manager step; "update every other global component". Recorded with the Toolkit revision in the source-to-contract coverage. A project that needs any of them declares it, and onboarding reports it as a project need.

**Refresh:** the list records the Toolkit revision it was written from. Workflow maintenance (policy 09) reviews it when the Toolkit's mandatory list changes.

## 4. Readiness command

`python3 -B scripts/workflow.py workspace status [--json]`, read-only.

- Finds installed clients (CLI on PATH, version, config directory) and the session's client.
- Calls each client's official listing once per run; one overall time budget; cells past it are `unknown (time-budget)`.
- One state per requirement × client: `ready`, `missing`, `outdated`, `needs-user`, `not-supported`, `unknown` (with reason). Hook trust and startup delivery are never `ready` from a listing.
- The plugin item in OpenCode is `not-supported` until R4.
- `workspace record` stores a small private result with its observation time, for the startup warning.
- Exit 0 ready, 1 not ready, 2 undetermined.

## 5. `prepare-my-workspace`

Modes: **verify** (run `workspace status` and report; writes nothing, not even the private records) and **prepare** (all steps). Identify the destination (host, container, VM, WSL, remote) first.

1. **Check:** run `workspace status`. Done: table produced.
2. **Tell:** list what is missing with its value; say you are proceeding; list coming user actions. Done: message shown.
3. **Back up:** each client config you will edit, outside projects, with restoration steps. Done: paths recorded. On failure: skip that client's edits and report.
4. **Install:** per missing item and client, read the tool's official instructions now (check its source and license), record source and date, run them. On failure: mark `failed`, continue with the rest. A source that cannot be read is a gap, never a stale copy.
5. **User actions:** one consolidated list; never approve for the user. Done: confirmed, or left `needs-user`.
6. **Verify:** rerun `workspace status`; start a new session per client and confirm loading. Done: every cell `ready`, `not-supported` or `needs-user`.
7. **Record and report:** `workspace record`, `upgrade record`; show the table, open user actions and recovery records.

Plugin upgrades use the [upgrade procedure](../../../skills/prepare-my-workspace/references/upgrade.md) inside step 4. Habblaud keeps its receiver reconciliation.

## 6. `onboard-my-project`

0. **Readiness:** run `workspace status`. For missing required items: announce what and why, run preparation for them as a separate operation, then resume. Items still `needs-user` are carried to the report; only work that depends on them waits. For an inspect-only or plan-only request, report what is missing and propose preparation without running it.
1. **Target and clients:** confirm the checkout (never the toolkit checkout just because it is current) and the clients to enroll (installed supported clients by default). Respect inspect-only and plan-only requests.
2. **Sources and inspection:** resolve the Toolkit revision once (resume at a recorded one; trusted offline copy marked unverified); inspect instructions, configuration, commands, project needs and baseline checks.
3. **Preferences:** the standing questions, one at a time.
4. **Plan:** assess the complete catalog; adapt the nine project documents; author the policy entry point. Every component ends planned, reused with evidence, inapplicable with reason, or blocked with prerequisite and resume condition.
5. **Apply:** back up, then recoverable file changes and startup membership per client; undo restores only content that still matches.
6. **Verify:** project checks; policy discovery (its four recorded layers); startup in a new session per client.
7. **Report:** per client — version, trust, startup observed; changes; pending user actions; undo location.

## 7. OpenCode as a full plugin client (decided 2026-11-10)

Designed in its own brief, `docs/superpowers/specs/2026-11-10-opencode-full-client-brief.md` (draft PR #190); brainstorming, review and the implementation plan continue in a separate session (slice R4).

## 8. Enforcement in code

- Readiness command: same answer every run.
- Plan validator (`runtime/onboarding.py` `_validate_proposal`): an applicable row may end `omit` only when it links a recorded user decision through `decision_ids` (for example an optional item the user declined); mandatory rows never end `omit`; `blocked` needs a non-empty prerequisite and resume condition.
- Startup warning: an enrolled client whose workspace record shows missing required items warns once, naming when it was checked.
- `validate.py`: the package version must differ from the latest release tag when packaged content changed since that tag.

## 9. Slices

| Slice | Delivers |
|---|---|
| R1 | Requirements list (JSON + view), readiness command, tests |
| R2 | `prepare-my-workspace` rewritten (modes, seven steps); Toolkit prompt no longer executed; removed items and their deviation record; `AGENTS.md` "wrap the full Toolkit" gains "except recorded local deviations"; policy 11 and `upgrade.md` reconciled with the consent rule; refresh trigger in policy 09 |
| R3 | `onboard-my-project` readiness step and step list; validator rule; policy 10 authority and handoff wording; `AGENTS.md` separation line allows the announced handoff |
| R4 | OpenCode as a full plugin client (separate session) |
| R5 | Startup readiness warning; version-bump check |
| R6 | Live check on this machine from OpenCode: readiness → preparation → resume → report |

Each slice: short design note, implementer, independent review, affected tests (`scripts/test_sharded.py`), full run for shared-core slices (R1, R4, R5), then merge.

## 10. Decisions (2026-11-10)

1. **Consent:** an onboarding or preparation request (not inspect-only or plan-only) authorizes installing the listed required items at machine or user scope, announced first. Costs, licensing, accounts, external data, authentication changes, sign-ins and trust approvals still wait for the user. Policies 10 and 11 and `upgrade.md` are amended to say this once.
2. **Docker and other unused foundation items** are removed (see §3).
3. **OpenCode** becomes a full client (R4).

## 11. Acceptance

- Onboarding on a machine missing a required plugin in any installed client announces it with its value, prepares it and resumes without a second request; independent onboarding work continues meanwhile.
- Every requirement × client cell is reported; unsupported ones carry a reason; no cell is `ready` without its required observation.
- Verify mode changes nothing; every edited client config has a backup and undo restores it; permissions, models and other plugins are preserved.
- A plan with an applicable component marked `omit` without a linked user decision is rejected.
- A client with lapsed trust or a stale copy is never reported ready.
- Readiness stays within its time budget; tests use fake client executables.
- The requirements list, preparation and onboarding contain no Docker or other removed item.
- Entry points stay within 150 words; Python files within 1000 lines.
