# Unconditional stop qualification — 14 November 2026

## Outcome

**Partial native qualification; final-stop verification is unfinished.**
The private account-free drill verified re-evaluation and one native child
release. A provider TLS failure stopped it before the final-stop cases.
The aggregate shared-settings preservation check also failed; attribution is
unavailable. Neither failure is counted as a pass or erased by source tests.

PR #302 remains draft/BLOCKED. PR #294 remains deferred, draft/BLOCKED,
unmerged and unreleased. No installed-package change, shared-service restart,
merge or release occurred.

## Subject and isolation

- Candidate: `b454d329799f13f35a912035601c34eb497ac8e0`.
- `index.ts` SHA-256: `e31248a1271f56488daf1bfe0779f27358147ffe0e37fe7ecdbfbd5310e4e426`.
- `gate.ts` SHA-256: `033273ee150b68ab0366003f4f159fa76c6491ac1e88544cf035c7835c03c942`.
- Original native OpenCode 2.2.6, directly owned `serve --stdio` process.
- Private HOME, XDG paths, configuration, SQLite database and temporary files.
- Anonymous public zero-cost `opencode/deepseek-v4.1-flash-free#max`;
  actual catalog and every native model request confirmed the route and variant.
- No saved credentials or Claude provider. Claude executable launches denied.
- Only public synthetic fixture data reached the model.
- Filesystem writes restricted to the private run directory. Separate self-checks
  proved outside-write denial inherited by a grandchild and Claude launch denial.
- Native permissions allowed only named fixture reads/edits, the literal unittest
  command, read-only status and original subagent dispatch. Tests and configuration
  were outside model tool-write permissions.

Opus independently reviewed the account-free isolation design. The first
four-request write pilot failed on relative permission matching; a separately
authorized four-request confirmation passed after correcting only the fixture.
Both receipts remain preserved. Neither was a full-stop test.

## Full-drill allocation and observations

One campaign allowed 120 model requests across all actors and request kinds,
200 controller operations and 45 minutes, with five minutes reserved for cleanup.
No retries or implicit renewal. Astra independently accepted the shortest
genuine `max` branch; the longer ladder remains source-test coverage.

| Step | Result |
| --- | --- |
| Immutable fixture self-check | Values 0–5 failed; 99 passed. |
| Three edit/check rounds | Six native canary edits and three failing checks. Both files were held at `reevaluation`, rounds 2. |
| Fresh native architecture child | Original foreground `subagent`, actual same-`max` route, no edits; successful child completion released both files once. |
| Fourth repair/check | Both files became `VALUE = 4`; immutable check failed as expected. |
| Fifth round | First model request failed before tools. |
| Final-stop and continuation cases | Not run. |

Usage: **26/120 requests**, all primary; **24/200 controller operations**;
**48,256 ms**. Four literal `python3 -B -m unittest test_canary` calls returned
exit 1. Eight successful native edit calls matched their permission source IDs.
Status snapshots reported no internal adapter error.

The failed event carried `provider.transport` and:

```text
EPROTO: The TLS handshake failed. Does the server speak TLS on this port?
```

The observer vetoed retry attempt 2. No second inference request or replacement
campaign was sent. This establishes a transport failure, not its network cause.
The private process exited zero, was reaped, and its process group was absent.
Its in-memory repair history is no longer available for native continuation.

## Preservation and evidence limits

Source, immutable test, instructions, feature settings, sandbox profile and private
configuration hashes matched. Shared service PID/version and selected Claude
directory metadata matched. **The aggregate shared-settings fingerprint did not.**
The controller retained only the aggregate comparison, not per-path before hashes.
It cannot identify the changed file or writer. The sandbox and inspected controller
constrain this campaign's writes, but do not prove all shared files stayed unchanged.
No shared file was restored or inspected for credentials or unrelated conversations.

Raw native records, exact actor/call IDs, checkpoint bytes, source/protected hashes,
process receipts and independent acceptance remain in private local evidence.
The original failure receipt is immutable; no claim of full preservation is made.

## Independent evidence assessment

Astra inspected the raw records, checkpoints, current fixture bytes, protected
inputs and runtime hashes. It accepted the partial coverage above, not full
qualification. No product regression was established within the observed steps.
Cleanup succeeded; shared-settings preservation remains unresolved.

The original allocation permits one campaign without retries or implicit renewal.
Astra confirmed that unused request capacity cannot authorize another attempt.
The TLS/network cause and changed shared-file identity remain unknown; neither
requires a product-code fix supported by this evidence.

## Remaining work

A separately authorized fresh native attempt must retain per-path before/after
fingerprints without recording secret contents. Preserve the 26 consumed requests,
failed receipt and all earlier closed allowances. Do not seed in-memory state,
replay the closed campaign, invent a supported variant or introduce approval UI.

Still required: final stop after renewed failures; repeated root/child edit refusal
with unchanged bytes/state and no approval prompt; unrelated-file/root work;
separate explicit root-user continuation clearing only the named file without
resuming an edit; then a distinct edit to 99 passing the same immutable check.

Late-completion races remain unit-covered, not observed by this sequential drill.
Claude-specific bridging, Desktop/TUI presentation, semantic diagnosis quality and
general workflow benefit are not qualified. Existing parser, fail-open, reload,
memory-only and shell/API bypass limits remain unchanged.
