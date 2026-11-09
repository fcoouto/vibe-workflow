# Onboarding authorization and readable configuration implementation plan

> **For agentic workers:** Use `superpowers:executing-plans` for the implementation and an independent final review. Steps use checkbox notation for actual completion.

**Goal:** Carry an authorized full onboarding request through routine setup and qualified activation without repeated consent, and explain the reviewed configuration in plain language.

**Architecture:** Keep authorization semantics in the onboarding policy. Route the skill and its existing inspect/apply procedures to that policy and one conditionally loaded presentation reference. Reuse main's file transactions and configured ordinary-entry behavior; do not create another approval record or replace runtime admission.

**Tech stack:** Markdown instructions; existing Python package validator and transaction/entry tests.

**Spec:** [Onboarding simplification design](../specs/2026-11-02-onboarding-simplification-design.md).

## Global constraints

- Base: reviewed main `a0f1767e36d3433df5b24154eaa34bf3dd8c398f`; isolated branch `codex/onboarding-flow`.
- Preserve the primary checkout, its branch features, unstaged edits and retained pilot history.
- Keep skill entry points at or below 150 words and each rule with one owner.
- Full onboarding authority covers only its actual agreed effects. Inspect, plan and configuration-only requests retain their limits.
- Keep exact frozen operation bindings, mandatory checks, source matching, recovery and later user edits intact.
- File-only configuration and undo require neither runtime birth nor native qualification.
- No installation, pilot activation, new service, registry, permission bypass or optional integration.
- This plan owns the independently implementable instruction/output slice. Completing it does not complete the wider runtime design or authorize an unqualified merge of that wider claim.

## Review focus

- A narrow planning request must not be interpreted as full onboarding authority.
- A full request must not trigger another confirmation solely because an internal transaction or stage changes.
- An additional cost, external scope or permission must remain a real decision when existing authority does not cover it.
- A configured-only or unknown native state must not be reported as active or complete.
- Human-readable output must preserve exact change locations, unresolved requirements, recovery limits and links to concrete technical records.

### Task 1: Establish the current baseline

**Files:** existing startup tests and package validator; no source edits.

- [x] Inspect current entry, transaction and instruction contracts and request the shared implementation boundary from Genesis.
- [x] Create an isolated worktree from reviewed main, preserving primary local changes.
- [x] Run `python3 -B -m unittest tests.test_unenrolled_checkout`; expect all existing configured/suspended/membership cases to pass.
- [x] Run `python3 -B scripts/validate.py`; package validation passes separately from native execution.

### Task 2: Implement the instruction and output contract

**Modify:** `policies/10-project-onboarding.md`, `skills/onboard-my-project/SKILL.md`, `skills/onboard-my-project/references/procedure.md`, `skills/onboard-my-project/references/apply-and-recovery.md`, `docs/policy-sources.md`.

**Create:** `skills/onboard-my-project/references/reviewed-configuration.md`.

**Consumes:** Actual user/session authority, the existing complete scoped proposal and current operation/evidence results.

**Produces:** The agent-written Reviewed Configuration document, with human-readable scope, changed paths and purpose, current status, next action, material decisions, recovery and technical record links. It is a presentation artifact, never authority.

- [x] Define full-request and limited-request authority once in onboarding policy. Require questions only for unresolved material exceptions.
- [x] Remove procedural wording that invents a separate conversational approval for every stage, while preserving independent verification of each exact operation.
- [x] Add the conditional presentation reference and route to it when presenting a reviewed configuration. Keep internal codes and hashes in technical records, with no new runtime schema.
- [x] Preserve configured-versus-active reporting and explicit missing qualification. Do not claim this instruction change supplies a production qualification reader.
- [x] Record user-authorized policy provenance and source-to-contract coverage.
- [x] Run package validation and local reference/anchor checks; expect valid links and all entry points within 150 words.

### Task 3: Independent behavioral qualification and review

**Read-only input:** Exact candidate diff, relevant policy/skill references and credential-free scenario facts. Reports remain in task-owned private storage.

- [x] Have a fresh native evaluator follow the candidate skill for five cases: full onboarding with passed checks, plan-only, configuration-only, a new paid/external effect, and missing native qualification with interrupted recovery.
- [x] Evaluate selected loads, next actions, authority boundaries and generated Reviewed Configuration. The first case must continue covered effects without another confirmation; narrow scopes and material exceptions must stop only dependent work; missing qualification must remain incomplete.
- [x] Give the evaluator the requests and raw facts, not the expected decisions. No real enrollment, external sends, provider calls or pilot changes.
- [x] Independently review the frozen diff for requirements, authorization/security consequences and preserved recovery. Fix supported blocking findings and rerun affected checks.
- [x] Record actual model/effort observations and limits. A fresh native evaluator proves only the observed instruction cases, not production hook activation.

### Task 4: Integrate the qualified instruction changes

- [x] Merge the independently qualified instruction/output candidate under the user's existing authorization. Keep the broader runtime design explicitly undelivered.

### Dependent runtime work: unresolved interface

- [ ] Obtain the agreed owner-issued qualification/current-observation interface and prior-active history source through Genesis coordination before touching shared runtime files.
- [ ] Write the concrete dependent runtime plan against those interfaces. Do not infer qualification from JSON, stored flags or a fixture callback, or introduce a signer/registry to fill an unknown contract.
- [ ] Prove the full design's runtime acceptance cases and applicable native observations in an authorized disposable scope. Preserve existing budgets and the reverted pilot.
- [ ] Review and qualify the runtime candidate before a separate merge; preserve unrelated branch work.

## Execution ledger

- User authorized implementation, review, qualification and merge on 2 November 2026. That instruction supersedes an additional routine plan-approval round.
- Coordinator model/effort remains unchanged and is not independently exposed. Recommendation: GPT-6.1 Sol High for implementation, GPT-6 Astra High for final review; current official guidance was checked. No API route or global model setting changed.
- Original baseline selector `tests.test_startup.CanonicalScopeTests` does not exist on main; corrected to the existing `tests.test_unenrolled_checkout` suite. This was a selection error, not a runtime failure.
- Shared runtime ownership/interface confirmation is pending. Independent instruction work may proceed; no activation proof is fabricated.

- Baseline: seven existing ordinary-entry tests passed in 2.097 s. Package validation passed; onboarding entry is 127 words.
- Independent instruction exercise completed all five cases; general/security review found no blocking instruction findings. All six reviewed hashes matched. No production runtime source changed.
- Genesis confirms this chat owns the proposed shared reader/classification/activation slice, but no production host issuer/read implementation exists. Core evidence/state/retention remains separately owned; the existing native campaign budget is not reused for a new lifecycle campaign.

- Ruling: integrate the independently qualified instruction/output slice separately while retaining the wider runtime work as unresolved. Its missing production observation/history contracts cannot be supplied by wording, fixture callbacks or invented authority. This delivers the approved simplification without claiming full activation delivery.
- Reviewer dispatch requested `gpt-6-astra` with `high` effort; the worker could not independently observe actual execution settings. Instruction evaluator inherited the coordinator settings; exact model/effort is unobserved.
- Evidence and remaining limits: [qualification record](../../onboarding-authorization-qualification.md).

- Final instruction candidate: 24 focused tests passed in 30.533 seconds; package validation, 100 links/anchors and unchanged six-file review hashes passed. Records-only review found no misleading completion claim.

### Resumed production observation work

- The instruction slice merged as PR #32 at `73fa953`; this checkout now matches that main baseline.
- Ruling: actual native notifications must reach the trusted qualification owner before a retained reader can exist. Extend the existing transport rather than inventing a receipt issuer or evidence format. `NativeProcess(..., on_notification=None)` and `_native_reader(methods, on_notification=None)` expose parsed stream notifications to an internal owner. Existing callers retain their behavior. The callback validates semantic scope; transport framing alone grants no authority. Observers must obey the existing deadline and retain no unbounded output.
- Files: `runtime/doctor.py`, `runtime/adapters/codex.py`, `tests/test_native_notifications.py`. No core evidence/state/transaction file edits.
- [x] Test first: four transport cases; three fail on the absent observer argument, default behavior passes.
- [x] Implement and verify notification delivery, framing rejection, observer rejection, output limits, invalid observer rejection, adapter forwarding and existing native reader consumers.
- [ ] Independent review; integrate only a qualified coherent candidate.
- [ ] Bind actual required hook observations and fresh control facts at the qualification owner, then implement supported durable consumption/history lookup. This is still unresolved; a transport callback cannot establish these facts.

- Qualification: initial four new cases passed with 22 existing doctor checks (26 total, 9.505 seconds). Final seven focused notification/adapter cases passed in 0.118 seconds. The consumer run executed 64 passing native-review/title tests in 110.741 seconds; its exit was nonzero solely because the requested `tests.test_native_workers` module does not exist. Worker cases live in `tests.test_native_reviews`; no nonexistent suite is claimed as passed. No test was rerun merely to erase that selection error.
- Package validation passed separately; client execution remains `not_checked`.
- Independent review found no demonstrated transport defect. Hold integration: no production owner consumes these notifications yet. The callback sees only frames encountered while awaiting replies, including initialize; it does not subscribe to another desktop connection, drain after the final reply, establish semantic scope, or authenticate execution claims. It is preparatory code, not delivered activation behavior. The internal observer must honor the shared deadline and protect private payloads.
- Remaining concrete contract sent to Genesis: identify existing owner-retained observation issuance/lookup and current control/history sources under the four-second read-only budget, or assess the exact existing operation-journal route. No evidence/state/changes source, installed settings, pilot or native campaign was altered. Keep this candidate unmerged until the consuming owner and full path qualify.

### Scoped collector delivery and native qualification

- Genesis approved the first runtime slice: bounded scoped HookStarted/HookCompleted input in the existing native transport, metadata/status only, unchanged discovery default. The user then explicitly authorized native qualification followed by merge.
- `HookCollector(thread_id, turn_id, source_path)` accepts exact selected bindings, pairs native start/completion metadata, limits 64 runs, rejects malformed/changed/duplicate pairs with an invalid latch, drops payload text, and returns frozen completed-pair snapshots. Hook scope and triggering turn binding are independent fields.
- Native attempt 1: ephemeral startup/read worked but emitted no hook events; unqualified. Attempt 2: native SessionStart exposed the incorrect scope/turn inference; unqualified. Regression failed before the correction and passed afterward. Ruling: permit one corrective attempt on the changed candidate, retaining the 90-second native execution ceiling; the original two-attempt count was an internal planning limit, not a user limit. No prior pilot campaign allowance was reused.
- Corrected native attempt: actual Codex CLI 0.165.0 owned app-server connection, genuinely enabled/trusted installed plugin source, disposable ephemeral checkout, one requested Sol High no-tool turn. Collected SessionStart and Stop completed pairs; turn completed in 14.058 seconds. SessionStart carried scope `thread` and a non-null triggering turn ID. No file changes were found in the disposable repository.
- Final focused proof: 72 tests passed in 24.094 seconds (12 notification/collector, 22 doctor, 38 native review/worker). A SQLite ResourceWarning occurred in the existing fixture suite; no test failed. This change does not touch database code. Package validation remains separate from native qualification.
- Native proof covers this collector only, using the installed hook definition as an actual event producer. It does not qualify this checkout's hooks as installed, desktop execution, admission, enforcement, retained/current observation lookup, active history or descendant-stop behavior.
- Source, observations and limits: [collector qualification](../../onboarding-hook-collector-qualification.md). Private sanitized observation/hash records remain with the task; no transcripts, credentials, environment dump or raw hook output were retained.

- Final review supersedes the earlier preparatory hold for this explicitly approved standalone collector slice. Reviewer recomputed the frozen source/test/installed-definition/native-result hashes; all matched. No blocking finding remained. Final package validation passed; its `client_execution: not_checked` field remains a package-validator limitation, with native observations reported separately above. Full-reader integration remains unresolved.
