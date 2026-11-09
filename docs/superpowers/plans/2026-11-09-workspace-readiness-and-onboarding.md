# Workspace readiness and onboarding — improvement plan

Date: 2026-11-09, revised 2026-11-10. Status: **draft for user review.** Supersedes the narrower [workspace client coverage plan](2026-11-09-workspace-client-coverage.md).

## 1. Problems observed

- Superpowers, Ponytail, Context7, RTK and the Graphify skill were never set up for OpenCode, the client in daily use: nothing asks which clients are installed.
- Onboarding ran on an unprepared workspace without saying so: it only "reuses" machine tools.
- Graphify was skipped as "pending": the plan validator accepts `omit` for an applicable component.
- Codex ran a stale plugin copy and its hook trust lapsed silently after an upgrade.
- Preparation executes the Toolkit's entire environment prompt, so it installs and checks things `vibe-workflow` never uses (Docker, Node toolchains), and the instructions grow with it.

## 2. Principles

1. **One short requirements list, owned here.** It names what the workflow needs and why. Both skills read it. The Toolkit is the source the list is authored and updated from, not a prompt executed at run time.
2. **Code checks, the agent acts.** A read-only command reports readiness the same way every time; the agent fixes what it reports with each tool's official instructions.
3. **Every installed client counts:** Codex, Claude Code and OpenCode, plus the client running the session.
4. **Required means required.** The user is told what is missing and the value it brings, then the agent proceeds. Only sign-ins, trust approvals, system passwords, purchases and authentication changes wait for the user.
5. **One gate, two skills.** Onboarding checks readiness first and hands off to preparation when needed, then resumes.
6. **Nothing silent.** Every requirement and catalog component ends in a named state; nothing is called working before a new session shows it.
7. **Less text.** Each rule is stated once; procedures are numbered steps with a done condition; anything the workflow does not use is removed.

## 3. Requirements list

New file `clients/workspace-requirements.md` (plain list) with a machine-readable twin used by the readiness command. Each item: name, why it matters (one sentence shown to the user), per-client check, where the official install instructions live, user actions.

**Required (blocks onboarding):**

| Item | Scope | Why |
|---|---|---|
| Python 3.11+, Git, GitHub CLI | machine | Run the plugin's hooks and checks; publish task branches and PRs. |
| `vibe-workflow` plugin, expected version, hooks trusted | each client | Loads the workflow rules at session start; version drift and lapsed trust leave a client running old rules. |
| Superpowers | each client | Design, planning, execution and review skills the workflow relies on. |
| Ponytail | each client | Smallest complete change by default. |
| Caveman | each client | Concise communication without losing technical substance. |
| RTK | each client | Compact command output, fewer wasted tokens. |
| Context7 | each client | Current library and tool documentation instead of stale memory. |
| Graphify | machine + each client skill | Local code map so agents find dependencies without reading many files. |

A tool with no official support for a client is reported `not supported` with the reason, never installed by improvisation.

**Project needs (from onboarding's inspection):** a runtime or tool the project itself declares (for example its language toolchain or package manager). Onboarding reports and, with the project's existing commands, checks them; they are not machine requirements.

**Removed:** Docker and every other Toolkit foundation item the workflow does not use. Recorded as a deliberate local deviation in `docs/policy-sources.md`.

## 4. Readiness command

`python3 -B scripts/workflow.py workspace status [--json]`, read-only, bounded time.

- Finds installed clients (CLI on PATH, version, config directory) and the session's client.
- One state per requirement × client: `ready`, `missing`, `outdated`, `needs-user`, `not-supported`, `unknown` (with reason).
- Reuses `runtime/package_versions.py` and each client's own listing commands.
- `workspace record` stores the last result privately so startup can warn cheaply.
- Exit 0 ready, 1 not ready, 2 undetermined.

## 5. `prepare-my-workspace`

1. **Check:** run `workspace status`. Done: table produced.
2. **Tell:** list what is missing with its value; say you are proceeding; list the user actions that will come. Done: message shown.
3. **Back up:** each client config you will edit, outside projects. Done: paths recorded. On failure: skip that client's edits and report.
4. **Install:** per missing item and client, follow the official instructions read now; record source and date. Done: command succeeded. On failure: mark `failed`, continue with the rest.
5. **User actions:** one consolidated list; never approve for the user. Done: confirmed, or left `needs-user`.
6. **Verify:** rerun `workspace status`; start a new session per client and confirm loading. Done: every cell is `ready`, `not-supported` or `needs-user`.
7. **Record and report:** `workspace record`, `upgrade record`; show the table and open user actions.

Upgrades use the existing [upgrade procedure](../../../skills/prepare-my-workspace/references/upgrade.md) within step 4.

## 6. `onboard-my-project`

0. **Gate:** run `workspace status`. If a required item is missing, tell the user what and why, run preparation for those items, then resume. Done: required items `ready`, or each remaining one listed as `needs-user`/`not-supported`.
1. **Target and clients:** confirm the checkout and the clients to enroll (all installed supported clients by default).
2. **Inspect:** instructions, configuration, project commands and needs, baseline checks.
3. **Preferences:** the standing questions, one at a time.
4. **Plan:** every catalog component ends planned, reused with evidence, inapplicable with reason, or blocked with prerequisite and resume condition. Done: validator accepts it.
5. **Apply:** recoverable file changes and startup membership per client. Done: read back.
6. **Verify:** project checks, policy discovery, startup in a new session per client.
7. **Report:** per client — version, trust, startup observed; changes; user actions; undo location.

## 7. OpenCode as a full plugin client (decided 2026-11-10)

OpenCode gets the same plugin behavior as Codex and Claude Code: it receives routing delegation to its own models (free and OpenCode Go plan), so it must load the workflow rules too.

- Install path: the plugin packaged as an OpenCode V2 plugin; the existing routing shim becomes part of it rather than a separate artifact.
- Startup orientation: inject the same enrollment context Codex and Claude Code receive, through OpenCode's supported plugin hooks.
- Enrollment and version checks: add `opencode` to the client lists in onboarding, startup, `package_versions` and the readiness command.
- Contracts: `clients/opencode.md` stops saying "not an onboarding or startup client"; the plan's support targets include OpenCode.

Routing delegation itself stays in the Smart Routing plan.

## 8. Enforcement in code

- Readiness command: same answer every run.
- Plan validator: no `omit` for an applicable component; `blocked` needs prerequisite and resume condition.
- Startup warning: an enrolled client whose last workspace record shows missing required items warns once.
- `validate.py`: a release whose content changed without a version bump is refused.

## 9. Slices

| Slice | Delivers |
|---|---|
| R1 | Requirements list, readiness command (`workspace status/record`), tests |
| R2 | `prepare-my-workspace` rewritten to the seven steps; Toolkit prompt no longer executed; Docker and unused foundation items removed |
| R3 | `onboard-my-project` gate and step list; validator rule |
| R4 | OpenCode as a full plugin client (install, startup, enrollment, version checks) |
| R5 | Startup readiness warning; version-bump check |
| R6 | Live check on this machine from OpenCode: gate → preparation → resume → report |

Each slice: short design note, implementer, independent review, affected tests (`scripts/test_sharded.py`), full run only for shared-core slices (R1, R4, R5), then merge.

## 10. Still to decide

1. **Consent:** an onboarding or preparation request authorizes installing the required items, announced first; purchases, licensing, authentication changes, sign-ins and trust approvals still wait for the user.
2. **AGENTS.md:** add one line: onboarding checks workspace readiness first and hands off to preparation.

## 11. Acceptance

- Onboarding on a machine missing a required plugin in any installed client announces it with its value, prepares it and resumes without a second request.
- Every requirement × client cell is reported; unsupported ones carry a reason.
- A plan with an applicable component marked `omit` is rejected.
- A client with lapsed trust or a stale copy is never reported ready.
- No instruction or check in the repository references Docker.
