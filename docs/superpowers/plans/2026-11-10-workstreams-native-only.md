# Workstreams native-only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delegated work runs only as native children; only the coordinator's thread gets a managed title, with no family code, member role or `workstream_context_id`.

**Architecture:** Shrink the title code to one format, make other shapes unmanaged, reject the retired field at the CLI, then shrink the contracts to match. No new modules.

**Tech Stack:** Python 3.11 standard library, `unittest` through `scripts/test_sharded.py`, Markdown contracts.

**Spec:** [docs/superpowers/specs/2026-11-10-workstreams-native-only-design.md](../specs/2026-11-10-workstreams-native-only-design.md)

## Global Constraints

- Managed title: `￻` + five meter cells + space + optional `STATUS · ` + topic. Examples: `￻▰▱▱▱▱ Thread naming`, `￻▰▰▰▰▱ REVIEW · Thread naming`.
- The status label appears only for `ATTENTION_STATES` with `user_attention`; `DONE` fills five cells, other states cap at four.
- A topic is printable, stripped, at most 160 characters and never contains ` · `.
- A title or saved record in any other shape is unmanaged and never written.
- `scripts/workstreams_family.py`, `runtime/delegation.py`, `runtime/adapters/codex.py` and `runtime/startup.py` stay byte-identical.
- Each Python file stays at or under 1000 lines; each skill entry point within 150 words.
- Contracts state each rule once and do not describe the old format.

## Review Focus

1. A current title in another shape (`￻▰▰▱▱▱ [W1] P · Topic`) when the saved one is managed: reconcile must pause, not rewrite. Test in Task 1.
2. A saved sidecar holding `family`/`member` reached by a hook's `repair`: no write and no exception escaping. Test in Task 1.
3. A title without the `￻` prefix (`▰▰▱▱▱ Topic`): not managed. Test in Task 1.
4. A topic that itself looks like a status (`REVIEW · Topic` passed as topic): rejected by `format_title`. Test in Task 1.
5. Review preparation through the CLI with `workstream_context_id`: rejected like worker preparation. Test in Task 2.

---

### Task 1: One title format

**Files:**
- Modify: `runtime/title_sync.py:11-45` (patterns, `format_title`, `_parsed`), `:48-67` (`reconcile`), `:158-178` (`_load`), `:181-300` (`operate`)
- Modify: `runtime/opencode_titles.py:136-153` (`_title`, `_blocked`)
- Test: `tests/test_title_sync.py`, `tests/test_title_sync_integration.py`, `tests/test_opencode_titles.py`, `tests/test_delivery.py`, `tests/test_compact_status.py`, `tests/test_delivery_reconcile.py`

**Interfaces:**
- Produces: `format_title(topic: str, status: str, cells: int, *, user_attention: bool = False) -> str`; `_parsed(title) -> dict | None` with keys `meter`, `status`, `topic`; `_load(path) -> dict | None`, `None` when the file is absent or the record holds `family` or `member`.

- [ ] **Step 1: Rewrite the format tests in `tests/test_title_sync.py`**

Replace every expected `[W1] P · ` / `[W1] C · ` string with the D4 form and every `format_title('W1', 'P', …)` call with `format_title(…)`. Delete the member/family tests (`:55-67`, `:69-95`) and the legacy-layout tests (`:145-175`). Add:

```python
def test_format_has_no_family_or_member(self):
    self.assertEqual(title_sync.format_title('Thread naming', 'WORKING', 1), '￻▰▱▱▱▱ Thread naming')
    self.assertEqual(title_sync.format_title('Thread naming', 'REVIEW', 4, user_attention=True),
                     '￻▰▰▰▰▱ REVIEW · Thread naming')
    self.assertEqual(title_sync.format_title('Review', 'DONE', 0), '￻▰▰▰▰▰ Review')
    with self.assertRaises(ValueError):
        title_sync.format_title('REVIEW · Topic', 'WORKING', 1)

def test_other_shapes_are_not_managed(self):
    for title in ('￻▰▰▱▱▱ [W1] P · Topic', '￻▰▰▱▱▱ REVIEW · [W1] C · X',
                  '▰▰▱▱▱ [W1] P · Topic · WORKING', '▰▰▱▱▱ Topic'):
        with self.subTest(title=title):
            self.assertIsNone(title_sync._parsed(title))

def test_other_shape_current_title_pauses(self):
    saved = self.saved() | dict(applied='￻▰▰▱▱▱ Thread naming')
    updated, desired = title_sync.reconcile(saved, '￻▰▰▱▱▱ [W1] P · Thread naming', self.task)
    self.assertTrue(updated['paused'])
    self.assertIsNone(desired)
```

`self.saved()` drops `family` and `member`.

- [ ] **Step 2: Run it and see it fail**

Run: `python3 -B scripts/test_sharded.py test_title_sync`
Expected: FAIL, `format_title()` takes the old positional arguments.

- [ ] **Step 3: Implement the format in `runtime/title_sync.py`**

- One pattern: `TITLE = re.compile(r'(?P<meter>￻▰{0,5}▱{0,5}) (?:(?P<status>[A-Z ]+) · )?(?P<topic>.+)')`. Remove `LEGACY_TITLE` and the `layout` field.
- `format_title(topic, status, cells, *, user_attention=False)` keeps the existing topic, status, cell and length checks, without the family and member checks.
- `_parsed` validates through `format_title` as today, so a topic containing ` · ` makes the title unmanaged.
- `reconcile` compares `('meter', 'status')` and calls `format_title(saved['topic'], …)`.

- [ ] **Step 4: Run the unit tests**

Run: `python3 -B scripts/test_sharded.py test_title_sync`
Expected: PASS.

- [ ] **Step 5: Rewrite the operation tests in `tests/test_title_sync_integration.py`**

Update expected titles to D4. Delete the member tests (`:100-130`, `:186-197`, `:354-398` child paths). Replace the corrupt-register test (`:152-162`) and add:

```python
def test_configure_never_touches_the_register(self):
    store = Path(os.environ['HOME']) / '.vibe-workflow' / 'workstreams-families.sqlite3'
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_bytes(b'corrupt family register')
    self.call('configure', topic='Thread naming')
    self.assertEqual(self.native.title, '￻▰▱▱▱▱ Thread naming')
    self.assertEqual(store.read_bytes(), b'corrupt family register')

def test_configure_rejects_member_and_parent(self):
    for extra in (dict(member='C'), dict(parent_session_id='parent')):
        with self.subTest(extra=extra), self.assertRaises(ValueError):
            self.call('configure', topic='Title', **extra)

def test_record_in_another_shape_is_unmanaged(self):
    self.call('configure', topic='Thread naming')
    path = title_sync._path(self.scope, 'codex', 'session')
    record = json.loads(path.read_text()) | dict(family='W1', member='C')
    path.write_text(json.dumps(record))
    before = self.native.title
    self.assertEqual(self.call('sync'), dict(ok=True, managed=False))
    self.assertEqual(title_sync.repair(self.scope, 'codex', 'session')['managed'], False)
    self.assertEqual(self.native.title, before)
```

`StateError` subclasses `StartupError`; if it is not a `ValueError`, assert `(ValueError, state.StateError)`.

- [ ] **Step 6: Run it and see it fail**

Run: `python3 -B scripts/test_sharded.py test_title_sync_integration`
Expected: FAIL on the register, member and other-shape tests.

- [ ] **Step 7: Implement `_load` and `operate`**

- `_load` returns `None` when the record has a `family` or `member` key; otherwise it requires `identity, topic, cells, applied, pending, paused, notified, applied_revision` (optional `topic_workflow_owned`) and validates `topic` with `format_title`.
- In `operate`, after `_load`: when `saved is None` and the action is `sync` or `status`, return `dict(ok=True, managed=False)`.
- Configure and resume optional fields are `('topic',)` plus `workflow_topic` for configure; remove the allocator import and call, the parent/member checks and the rebind and resume family/member checks. A new record has no `family` or `member`.

- [ ] **Step 8: Update OpenCode titles**

In `runtime/opencode_titles.py`, `_title` drops the `layout` check (the `￻` prefix check stays) and `_blocked` compares `('meter', 'status')`. In `tests/test_opencode_titles.py` replace every `[W1] P · ` title with the D4 form; add `test_d4_title_accepted`: `_title('￻▰▱▱▱▱ Topic')` raises nothing and `_title('￻▰▱▱▱▱ [W1] P · Topic')` raises `ValueError`.

- [ ] **Step 9: Update the remaining expected strings**

In `tests/test_delivery.py`, `tests/test_compact_status.py` and `tests/test_delivery_reconcile.py`, replace expected `[W1] P · ` titles and saved `family`/`member` fields with the D4 form.

- [ ] **Step 10: Run the affected modules**

Run: `python3 -B scripts/test_sharded.py test_title_sync test_title_sync_integration test_opencode_titles test_delivery test_compact_status test_delivery_reconcile`
Expected: PASS. Also `grep -rn "workstreams_family\|\[W1\]" runtime tests --include='*.py'` returns only `runtime/state.py:266` and the tests of the unchanged script and startup (`tests/test_startup.py:464,517`, `tests/test_workstreams_cli.py:226-260`), which stay.

- [ ] **Step 11: Commit**

```bash
git add runtime/title_sync.py runtime/opencode_titles.py tests/
git commit -m "feat(titles): one coordinator title format; other shapes stay unmanaged"
```

### Task 2: Retire `workstream_context_id` at the CLI

**Files:**
- Modify: `scripts/workflow.py:298` (delegation prepare/coordinator) and `:314-317` (evidence review prepare)
- Test: `tests/test_workstreams_cli.py:62-80`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `StartupError("workstream_context_id is retired; native children use their attempt name")` from both prepare paths.

- [ ] **Step 1: Write the failing tests**

Rename `test_ordinary_worker_context_marker_survives_native_lifecycle_with_features_off` to `test_ordinary_worker_uses_attempt_name_with_features_off`. Replace its first nine lines with:

```python
code, rejected = self.cli('prepare', self.f.prepare_request(workstream_context_id='WS42.01'), ordinary=True)
self.assertEqual(code, 1, rejected)
code, child = self.cli('prepare', self.f.prepare_request(), ordinary=True)
self.assertEqual(code, 0, child)
self.assertEqual(child['task_name'], 'worker_' + child['attempt_id'])
```

Keep the observe and reconcile lifecycle that follows. Add a review test that runs `workflow.py evidence review prepare --cwd <root>` through the same patching as `cli` with stdin `{"workstream_context_id": "WS42.03"}` and asserts exit code 1.

- [ ] **Step 2: Run them and see them fail**

Run: `python3 -B scripts/test_sharded.py test_workstreams_cli`
Expected: FAIL, both prepares accept the field.

- [ ] **Step 3: Add the two checks in `scripts/workflow.py`**

Raise the error above when `"workstream_context_id" in request`: next to the existing check at `:298`, and before the review operation is called when `args.action == "review" and args.review_action == "prepare"`.

- [ ] **Step 4: Run the affected modules**

Run: `python3 -B scripts/test_sharded.py test_workstreams_cli test_workstreams test_native_worker_discovery`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/workflow.py tests/test_workstreams_cli.py
git commit -m "feat(delegation): reject workstream_context_id for new assignments"
```

### Task 3: Shrink the contracts

**Files:**
- Modify: `policies/03-agent-delegation.md`, `policies/07-context-and-handoff.md:35`, `clients/workstreams-naming.md`, `clients/workstreams-titles.md`, `clients/workstreams.md`, `clients/workstreams-protocol.md:59-71`, `clients/evidence-review-prepare.md:10`, `skills/workstreams/SKILL.md`, `docs/policy-sources.md`
- Regenerate: `clients/instruction-map.json`

**Interfaces:**
- Consumes: the D4 format from Task 1 and the CLI rejection from Task 2.

- [ ] **Step 1: Edit the contracts as the spec's section 4 table lists**

Policy 03: native children only, one coordinator schedules all, a requested chat is an independent conversation; remove `:60-61` mapping and `:77-79` peer push. Naming: coordinator only, the D4 format, statuses, meter, PR merge gate. Titles: coordinator-only adoption and sync; no member, parent binding or family text. `workstreams.md`: add "branch or worktree" to the child record, remove `:80`. Protocol and review-prepare: native children use the attempt name; no context ID. Keep `SKILL.md` under 150 words.

- [ ] **Step 2: Regenerate the map and validate**

Run: `python3 -B scripts/read_instructions.py --prepare && python3 -B scripts/validate.py`
Expected: `"validation": "passed"`.

- [ ] **Step 3: Check the wording is gone**

Run: `grep -rnE 'workstreams_family|member: "C"|deliverable chat|peer chat|\[W[0-9<]|WS42' clients policies skills --include='*.md'`
Expected: no output. Read `clients/workstreams-titles.md` once for any remaining title `parent_session_id`.

- [ ] **Step 4: Check the instruction paths**

From the policy index, follow two representative tasks and record the files reached: "implement a bug fix as coordinator" reaches the naming rules and the coordinator title; "worker brief for a native child" reaches Agent Delegation and no title procedure.

- [ ] **Step 5: Commit**

```bash
git add policies clients skills docs/policy-sources.md
git commit -m "docs(workstreams): native children only; coordinator-only titles"
```

### Task 4: Verify and publish

- [ ] **Step 1: Affected modules and full suite** (`scripts/workflow.py` is shared core)

Run, with the checkout unchanged during the run: `python3 -B scripts/test_sharded.py` then `python3 -B scripts/test_sharded.py --fast`
Expected: both pass. Rerun any failure alone before judging it.

- [ ] **Step 2: One live OpenCode observation** (reported separately; does not gate merge)

In a new OpenCode session, continue a child created by an earlier session by passing its `sessionID` to `subagent`. Record whether it continues the same child. This answers the spec's "coordinator lost" risk.

- [ ] **Step 3: Independent review, then publish**

Request an independent read-only review of the branch diff against `main`. Apply blocking and should-fix findings, rerun affected modules, push, update PR #200's body with checks and review status, and set `[READY]` only when readiness holds.
