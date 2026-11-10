# Stable machine bindings and the hook delivery note

Date: 2026-11-10. Found during workspace readiness R6; approved by the user ("relax machine-related bindings; keep what is essential; keep it simple").

## Problem

1. Enrollment records bind a checkout by `(device, inode)` of its root and Git directory. macOS renumbered the volume (`st_dev` 16777234 → 16777232, inodes unchanged), so the accepted record now reads as `identity_changed` and every session in this repository lost its startup orientation. The same can happen after any reboot or remount.
2. The hook delivery note is written only after a successful startup, so a client whose hooks run but whose enrollment fails never proves trust.

## Rule

- **Keep** device-and-inode comparisons that happen within one process or one operation (open, then confirm it is the same file; detect a swap between check and use). Device numbers are stable for that span, and these checks are what make file operations safe.
- **Relax** comparisons against values persisted earlier (enrollment records, task/state records, settings, ledgers, evidence, recovery journals): a binding matches when the inode matches and the canonical path that the record names is the one being checked. The device number is not compared. Keep writing it, so existing records and schemas stay valid.
- A copied or moved checkout still fails: a copy has new inodes; a move changes the canonical path.
- One shared helper owns the persisted comparison; callers stop comparing raw dictionaries.

## Delivery note

Write the note whenever the `SessionStart` handler runs for a supported client, including when it returns a diagnostic. Trust is per client, not per project. All other note rules (private, bounded, deadline, silent failure) stay.

## Acceptance

- An enrollment record whose stored device differs but whose inodes and paths match is accepted; a copied checkout (different inode) and a moved one (different path) are still rejected.
- Every in-operation device-and-inode check is unchanged.
- A diagnostic startup still writes the delivery note.
- Full suite once on the final candidate (shared core changes).
