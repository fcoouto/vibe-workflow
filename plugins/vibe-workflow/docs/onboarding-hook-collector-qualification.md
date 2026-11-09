# Scoped native hook collector qualification

2 November 2026. First runtime slice following the onboarding instruction changes in PR #32. This delivers bounded native event collection, not the complete production activation reader.

## Contract and source

`runtime.doctor.HookCollector` receives notifications from the existing owned `NativeProcess` stream. It selects exact thread, nullable triggering turn and source path; accepts synchronous plugin command hooks; pairs matching start/terminal metadata; limits collection to 64 runs; and returns immutable metadata snapshots. Malformed, duplicate or changed matching runs invalidate collection. Other sources/targets produce no observation. Output text, transcripts, environment and status messages are omitted.

`NativeProcess` and the Codex native-reader context manager expose an opt-in internal notification callback. Existing discovery/review/title readers retain their default behavior. The trusted callback must obey the shared deadline and handle private input safely. Transport byte/frame/deadline limits still apply.

Hook scope and triggering turn are independent. The actual SessionStart run was thread-scoped while carrying the triggering turn ID. The collector does not infer one field from the other. Run identifiers are bounded at 4096 characters; the observed IDs were shorter than the original 256-character limit.

## Native observations

The user authorized native qualification and merge. This separate collector campaign used a disposable, unenrolled checkout and ephemeral native sessions. It preserved saved settings and the reverted pilot. The native executable was Codex CLI 0.165.0, SHA-256 `a45099154d7fb4a5ed8a56f40966a95deb938e508bab23e6985035c709e9a85a`.

The genuinely enabled/trusted installed Toolkit plugin supplied native events; no inline hook substitution, trust bypass or installed-package update was used. Its hook definition SHA-256 was `bb1dda06efd9529e8a21a05eff4831a50b6223e366baf09e9828df86e4c71a3d`. Candidate Python collection code was imported from this checkout.

| Attempt | Observation | Qualification |
| --- | --- | --- |
| Ephemeral startup/read, no model turn | Correct checkout returned; no hook events | Unqualified |
| One turn with initial collector | Actual SessionStart rejected the incorrect scope/turn inference | Unqualified; candidate fixed with regression |
| Corrected collector, one requested Sol High turn | Selected SessionStart completed in 200 ms; Stop completed in 142 ms; turn completed in 14.058 s | Scoped collection observed |

The third attempt was a documented corrective rerun on changed code under the user's authorization, retaining the 90-second native execution ceiling. Earlier pilot budgets were not reused. Requested model/effort are reported as requested settings, not independently authenticated execution settings. The disposable repository remained clean.

## Software proof and limits

Seventy-two focused tests passed in 24.094 seconds: 12 transport/collector cases, 22 doctor cases and 38 native review/worker cases. Cases include interleaved stream delivery, wrong scope/source exclusion, malformed and mismatched pairs, duplicate completion, private-output omission, immutable snapshots, collection/transport bounds and unchanged default consumers. Test-first failures were observed before adding the callback/collector and before fixing scope handling. A fixture SQLite ResourceWarning was observed; no test failed. Final package validation is recorded in the pull request.

Callbacks see frames encountered while awaiting RPC replies; they do not replay history, subscribe to another client's connection or automatically drain after the last reply. The qualification owner kept the producer connection active with bounded reads until the selected native turn completed. Pending starts are omitted from snapshots; a snapshot is not proof that every hook or descendant stopped. `completed` is the native run status, not onboarding success.

This evidence does not qualify desktop hooks, all enforcement cases, installed candidate behavior, production admission, trusted retained lookup, control freshness, prior-active history or full activation. Those remain separate work owned by onboarding with Genesis coordinating core interfaces.
