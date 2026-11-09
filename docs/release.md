# Publish a release

Codex installs releases from the `codex/marketplace-release` branch (see the [README](../README.md#prepare-your-workspace)). The branch holds a built package, not the repository: marketplace files at its root and the plugin under `plugins/vibe-workflow/`, without `tests/` or its runner. Clients copy the plugin folder, so a clone's `.git` stays outside every installed copy, and the copy fits the 8 MiB package scan Codex fixtures use. Each release is one commit on top of the previous release, so `codex plugin marketplace upgrade` keeps fast-forwarding. Run this only when a release is requested.

1. Merge the release PR into `main` (version bump in both plugin manifests and `docs/release-notes.md`) and tag its merge commit with the version.
2. In a clean checkout of that tag, fetch the branch and build:

   ```sh
   git fetch origin codex/marketplace-release
   python3 -B scripts/package.py --release --commit-onto origin/codex/marketplace-release
   ```

   The build refuses tracked changes, validates the plugin and writes it to `~/.cache/vibe-workflow/release-package`. `release_commit` in the JSON report is the new commit; no branch moves.
3. Inspect it: `git show --stat <release_commit>` and `git ls-tree --name-only <release_commit>`. The root lists `.agents`, `.claude-plugin`, `LICENSE` and `plugins`.
4. Publish: `git push origin <release_commit>:refs/heads/codex/marketplace-release`. This is a fast-forward; never force-push the branch.
5. Check an install in a disposable `CODEX_HOME`: `codex plugin marketplace add https://github.com/fcoouto/vibe-workflow.git --ref codex/marketplace-release`, then `codex plugin add vibe-workflow@vibe-workflow-local`. The installed copy has no `.git` or `tests/`.

Users already on the branch run `codex plugin marketplace upgrade vibe-workflow-local`. Codex then refreshes the installed copy, including the first time the layout changes, and asks to approve the changed hooks. Evidence: [dev package validation](dev-package-validation.md#release-layout).
