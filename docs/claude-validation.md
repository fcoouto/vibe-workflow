# Claude startup validation

25 October 2026. Scope: reuse the validated startup layer for another native client. Onboarding, enforcement, model routing and connector implementation remain later work.

## Candidate and automated proof

Claude runtime/manifest SHA-256: `c6224e1dad862fceb223d9e2da19b4b35fe7facaa294cd4ebb0f9dbbbb6ad79d`. The digest concatenates each ordered relative filename, NUL, exact bytes, NUL: `.claude-plugin/plugin.json`, `hooks/claude-code.json`, `scripts/workflow.py`, `runtime/startup.py`, `runtime/adapters/claude_code.py`, `runtime/adapters/session_start.py`, `runtime/adapters/codex.py`.

Policy digest: `e955274b00b5dbf75f266677e99da138f0c3da835e8c23ed7fc1e34c03be429d`; approved policies are unchanged.

- Package validation passed.
- 54 tests passed. Both native adapters run the shared acceptance cases, including checkout/worktree isolation, wrong-client enrollment, malformed records, settings preservation and hostile Python/Git environments.
- Host environment hints cannot activate an unrelated checkout or replace native client enrollment.
- Package checks reject duplicate Claude default hooks, divergent manifest versions and paths escaping the package.
- `git diff --check` passed.
- Independent code/security review found no actionable issues in the adapters, shared parser, routing, manifests, validator or host guidance; the reviewer independently ran all 54 tests and package checks.

## Native execution

| Surface | Evidence |
| --- | --- |
| Claude Code CLI 2.1.304 | Strict plugin validation passed with zero errors/warnings. One enrolled startup hook supplied local context. Unrelated and Codex-only enrollment produced no context. Malformed enrollment produced a diagnostic. Disabled hooks did not execute. |
| Codex CLI 0.163.0 | Regression probe passed: one enrolled context injection, unrelated silence, malformed diagnostic, untrusted/modified definitions prevented execution. |
| Codex desktop-bundled app-server 0.164.0-alpha.11.1 | Same regression cases passed; modified content was rejected with an approval-required failure event. |

All probes used disposable homes/configuration and repositories, an environment allowlist without credentials, and denied network access. No real client settings, authentication or project enrollment changed. Processes were stopped afterward.

Claude used `--init-only --plugin-dir <copy> --setting-sources user`, which runs startup without starting a conversation. Native debug output confirmed one handler and accepted context. The final enrolled observation took 50 ms; this is one observation, not a performance distribution. Codex used the previously inspected separate stop hook. No probe generated a model response.

Adding license-holder author metadata resolved the initial strict-validation warning. Strict validation and one enrolled Claude startup were rerun against that final manifest. Other cases remain applicable because runtime, registration and enrollment behavior were unchanged. The validator's marketplace-install advice remains advisory; local `--plugin-dir` loading was qualified, not published marketplace distribution.

## Limits

Claude Desktop is not installed here. Desktop Code, Cowork, desktop UI behavior and model policy compliance remain untested. Native startup success does not qualify those surfaces or later enforcement features.

Sources: [Claude hooks](https://code.claude.com/docs/en/hooks), [Claude plugin manifest](https://code.claude.com/docs/en/plugins-reference).

## D8 qualification, stage 1 (9 November 2026)

Claude Code 2.1.311, `haiku`, a Claude-born canonical disposable checkout, real `HOME`. One capped run (about $0.003) was observed with `--output-format stream-json --include-hook-events`:

- The `init` event listed `vibe-workflow` 0.1.33 from the package path (`vibe-workflow@inline`) and the client version.
- `SessionStart:startup` returned the real startup context (710 bytes) naming the policy index and the checkout's authority. `UserPromptSubmit`, `PostToolUse:Read` and `Stop` also ran and exited 0.
- That stream was collected into a retained receipt, persisted through D4's owner path and read back as an `active` qualification-only scope with only `startup-context` verified.

Not observed: pre-tool, post-tool and stop behavior under an enrolled bound task, Desktop and Cowork, older Claude versions, and more than one run. The PostToolUse and Stop diagnostics in that run came from a synthetic-qualified fixture state, not from the qualified scope. Package tests use synthetic streams shaped like the recording and do not spend usage.

## D8 subagent observation (9 November 2026)

Claude Code 2.1.311, `haiku`, a Claude-born canonical checkout activated by a real stage-1 receipt, real `HOME` and real managed-policy paths. One run (about $0.01) spawned a subagent whose first prompt line was the assignment marker and whose final message was the review report:

- The packaged `PostToolUse:Agent`, `SubagentStart` and `SubagentStop` hooks ran and exited 0; the spool recorded one agent.
- The chain verified and `collect_review` returned the receipt (mechanism `claude-code-subagent-hook`, requested model `haiku`, observed `claude-haiku-5-5`, the spawn tool call and reviewer ids) and the report.
- An earlier run with 64-character digests failed correctly: the model dropped one character when echoing `candidate_digest`, so the report did not match its assignment. Model echo reliability matters; use a model that copies digests exactly, and do not relax the match.
- Setup artifact: a test that patched `MANAGED_DIRECTORIES` to empty produced a different configuration digest than the real hook subprocess. Real use is consistent because collection and reading see the same paths.

Not observed: D6 ledger integration, background versus synchronous completion ordering beyond this run, Desktop and Cowork, older Claude versions.

## What S1, S3 and S4 do and do not establish (9 November 2026)

Their tests run on Claude-born canonical scopes whose synthetic trusted-owner qualification carries no capability projection, so admission is ordinary, as for Codex fixtures. That shows package behavior only. For a real scope activated by the stage-1 receipt (only `startup-context` verified) the retained admission check was run directly and refused every managed operation:

| Operation | Needs | Result |
| --- | --- | --- |
| `task-start`, `task-patch` | `pre-tool-enforcement` | refused: pending capability needs an exact qualification attempt |
| `check-run`, `verify-completion` | `review-readiness` | refused: pending capability needs an exact qualification attempt |

So a real Claude project cannot yet use the guard, task controls, checks or completion without an admitted qualification attempt. Closing this needs N4 stage 2 (managed pre-tool, post-tool and stop behavior) and review readiness, which in turn need the D6 integration. Native hook execution of the guard, post-tool and stop handlers under a bound task remains unobserved.

## Rebase onto main and a pre-existing size problem (9 November 2026)

The branch was rebased onto `origin/main` (215 commits ahead; two mechanical conflicts in `scripts/workflow.py` and the generated instruction map). To meet the new Python file-size ratchet, `_public_canary_patch` moved unchanged to `runtime/setup_canary.py` (re-exported under its old name) and the Claude setup code lives in `runtime/setup_claude.py`; three ceilings were lowered and none raised.

**Pre-existing on `main`, not caused by this branch:** the tracked package is now 9.46 MB (7.76 MB at the old fork point), above the 8 MiB total-bytes cap that `_package_scan` enforces when it hashes the plugin bundle. Seven Codex tests that hash the real package fail on a pristine `origin/main` and on this branch alike: six in `test_staged_setup` and `CooperativeQualificationTests.test_guarded_worker_check_and_review_complete_only_the_qualification`. The same scan runs for production Codex qualification, so Codex qualification reads on the real package are likely affected. That needs its own fix (for example scoping the Codex bundle or raising the cap deliberately). Claude avoids the problem by hashing only the directories it loads (about 3.7 MB).

A differential run (`test_setup_source`, `test_setup_observation`, `test_native_bootstrap`, `test_staged_setup`, `test_package_versions`; 93 tests) gave identical failing sets on `origin/main` and on this branch.
