---
name: prepare-my-workspace
description: Verify, prepare, repair, or upgrade machine-level tools and agent integrations in every installed client. Not project onboarding.
---

# Prepare my Workspace

Modes: **verify** (report only; writes nothing, not even records) and **prepare**. Identify the destination first (host, container, VM, WSL, remote). Requirements come from `workspace status`, not the Toolkit prompt.

Load the [procedure](references/procedure.md) before step 1: it holds each step's done condition, failure handling and consent. Plugin upgrades use the [upgrade procedure](references/upgrade.md) in step 4; Habblaud keeps its receiver reconciliation.

1. Check
2. Tell
3. Back up
4. Install
5. User actions
6. Verify
7. Record and report

An unreadable source is a gap, never a stale copy.
