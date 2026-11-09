# Install and reconcile the Habblaud receiver

Load this procedure when a selected Habblaud installation is created, discovered
during onboarding, or updated. Complete receiver setup before qualifying the
[existing start hook](worker-visibility.md#forward-the-original-desktop-start-event).
Verification-only work inspects and reports the current state.

## Apply the bundled receiver patch

The package includes [receiver.patch](../assets/habblaud/receiver.patch), its
[provenance](../assets/habblaud/receiver.json), and the upstream
[MIT license](../assets/habblaud/LICENSE). It carries the reviewed receiver,
badge rendering and regression tests. The patch is qualified against Habblaud
1.0.3 at the recorded upstream commit. Routine setup uses these packaged files.

1. Identify the selected installation's source directory, version, launch method
   and configuration owner. For a fresh install, obtain the selected upstream
   source using its installation procedure. A prebuilt image must be rebuilt
   from compatible patched source to include this receiver.
2. Preserve a recoverable copy of affected source files and deployment
   configuration outside the source tree. Record the installed bundle or image,
   source revision, existing edits and package patch digest. Retain the current
   runtime data and account configuration.
3. Set the two absolute paths below from the observed source and workflow package.
   Run the reverse check first. Success means the bundled changes are already
   present, so skip application and continue with verification.

   ```sh
   habblaud_source=/absolute/path/to/habblaud
   receiver_patch=/absolute/path/to/workflow/assets/habblaud/receiver.patch
   git -C "$habblaud_source" apply --reverse --check "$receiver_patch"
   ```

4. If the reverse check fails, check the forward patch. Apply it only when that
   check succeeds. Git's complete-patch application preserves unrelated edits.

   ```sh
   git -C "$habblaud_source" apply --check "$receiver_patch"
   git -C "$habblaud_source" apply "$receiver_patch"
   git -C "$habblaud_source" apply --reverse --check "$receiver_patch"
   ```

   If both checks fail on the previously bundled receiver, check and apply
   [the upgrade delta](../assets/habblaud/receiver-upgrade.patch) in the staged
   source. Verify its digest and `upgrade.from_patch_sha256` in the provenance
   against the previous full bundle. Require `git apply --check` before applying
   the delta and the full patch's reverse check afterward.

   If these checks cannot establish a complete update, report the receiver as
   incompatible or partially patched.
   Inspect the changed upstream implementation or local edits before preparing
   an updated patch. Preserve the installation and continue independent setup.
   Do not force hunks, create rejects, reset user files or treat an unverified
   receiver as ready. An upstream implementation that already supplies these
   capabilities needs equivalent regression proof before skipping the patch.
5. Install dependencies using Habblaud's committed lockfile and supported runtime.
   Run the focused receiver tests, type checks, translations and build from that
   source directory.

   ```sh
   npm ci --no-audit --no-fund
   npm test -- server/sources/codex/source.test.ts client/src/ui/model.test.ts
   npm run typecheck
   npm run i18n:check
   npm run build
   ```

6. Deploy the verified build through the installation's existing launch method.
   For Docker, rebuild the Habblaud service with its selected Compose files,
   then replace only that service while retaining its configured volumes.
   For a source installation, restart the existing process using its configured
   service manager. Verify health and that the running bundle or image is the
   one just built. Record the recovery location and deployed identity.

## Restore previously saved declarations

Keep the receiver's `codex-badges.json` with its existing data directory.
It restores labels for verified native children after restart without creating
agents or changing their activity. To carry forward a declaration observed
before the receiver could persist labels, replay its original private context:

```sh
printf '{}\n' | python3 -I -B <package>/scripts/worker_visibility.py restore \
  --context <existing-observed-context> \
  --visibility-plugin <trusted-installed-local-model-delegation-root> \
  --habblaud-endpoint <literal-loopback-HTTP-url> \
  --habblaud-account <observed-Codex-account-id>
```

Use only saved contexts with an observed native child. The receiver verifies
the child and immediate parent in the selected account. It will not overwrite
a different saved badge. Unobserved or unavailable records keep the standard
fallback; never guess a role or replay a start event to restore a badge.

## Verify and retain during upgrades

The receiver must accept the declared `Role · Model Display Name` for the
matching subagent, account and parent. It must retain the original generic
fallback for unassigned children, suppress the native parent role badge and
reject mismatched or malformed declarations. The bundled tests cover native
rollout and hook registration, including hook-only routed children.
They also cover nested immediate-parent reconciliation, the bounded display
grace for new generic children, preserved early activity and the original
fallback when no start label arrives. Existing agents continue immediately;
the grace never changes execution or renews on later tool events.
Check restored badges using a separate receiver process with retained test data.
Check a reused child with and without a saved declaration. Verify its identity,
role and activity remain correct without a new start or a second declaration.

Reconcile the existing hook after receiver deployment. Observe a real authorized
fresh child and its first badge before claiming live delivery. Fixture tests,
a successful build and an HTTP health check do not establish that observation.
Never send synthetic workers to a live office.

Every Habblaud source or image upgrade repeats this procedure before replacing
the running build. Reuse already-applied changes when the reverse check passes.
Qualify changes to the receiver source through the focused checks above. Keep
the previous verified deployment available until the replacement is healthy.
If verification fails, restore that deployment and report the display
qualification as pending. Role display never determines whether a worker runs.
