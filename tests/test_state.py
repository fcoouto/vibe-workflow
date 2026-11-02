"""Disposable private state; no model, client, enrollment or settings changes."""

from contextlib import closing
from dataclasses import replace
import importlib
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from runtime.engine import RuntimeEvent
from runtime.startup import ProjectScope, RuntimeSettings


PACKAGE = Path(__file__).resolve().parents[1]


def canonical_scope(legacy, *, client="codex", repository_id=None, checkout_id=None,
                    primary_root=None, git_dir=None):
    """Reuse born authority for seam projections; legacy inputs remain synthetic negatives."""
    from runtime import state
    from runtime.startup import AcceptedScope, _canonical_freeze, policy_digest
    if isinstance(legacy, AcceptedScope):
        from runtime.startup import _canonical_plain
        identity = _canonical_plain(legacy.accepted_identity)
        updates = dict(client=client)
        for key, value in (("repository_id", repository_id), ("checkout_id", checkout_id),
                           ("primary_root", primary_root), ("git_dir", git_dir)):
            if value is not None:
                updates[key] = value
        for key, value in updates.items():
            identity[key] = str(value) if isinstance(value, Path) else value
        identity["repository_root"] = str(legacy.repository_root)
        bindings = _canonical_plain(legacy.bindings)
        for key, value in (("root", legacy.repository_root), ("primary_root", primary_root), ("git_dir", git_dir)):
            if value is not None:
                info = value.stat()
                bindings[key] = dict(device=info.st_dev, inode=info.st_ino)
        identity["bindings"] = bindings
        return replace(legacy, **updates, bindings=_canonical_freeze(bindings),
            accepted_identity=_canonical_freeze(identity), accepted_scope_digest=state._digest(identity))
    repository_id = repository_id or "repository"
    checkout_id = checkout_id or "checkout"
    root, common = legacy.repository_root, legacy.git_common_dir
    primary, private = primary_root or root, git_dir or common
    for path in (root, common, primary, private): path.mkdir(parents=True, exist_ok=True)
    def binding(path):
        info = path.stat()
        return dict(device=info.st_dev, inode=info.st_ino)
    bindings = {"root": binding(root), "primary_root": binding(primary),
                "git_dir": binding(private), "git_common_dir": binding(common)}
    runtime = RuntimeSettings(3, legacy.runtime.state_dir.with_name(legacy.runtime.state_dir.name + "-canonical"))
    policy, digest = policy_digest(state.PACKAGE), state.runtime_fingerprint()
    identity = dict(version=1, project_id=legacy.project_id, repository_id=repository_id, checkout_id=checkout_id,
        project_root=str(primary), project_binding=binding(primary), primary_root=str(primary), repository_root=str(root),
        git_dir=str(private), git_common_dir=str(common), bindings=bindings, client=client,
        membership=dict(included=True, authorization=dict(reference="fixture:standing", sha256="a"*64)),
        configuration=dict(policy_digest=policy, runtime_digest=digest,
                           runtime=dict(schema_version=3, state_dir=str(runtime.state_dir))), shared_policy=[])
    scope_digest = hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()
    return AcceptedScope(legacy.project_id, repository_id, checkout_id, primary,
        primary / ".vibe-coding-workflow/project.json", _canonical_freeze({}), 1, root, primary, private, common,
        _canonical_freeze(bindings), client, policy, digest, runtime, _canonical_freeze(identity), scope_digest,
        _canonical_freeze(dict(kind="trusted-coordinator", reference="fixture:synthetic", sha256="b"*64)))


class StateTests(unittest.TestCase):
    def setUp(self):
        self.fresh_scope()
        self.request = dict(client="codex", session_id="session", task_id="task", scope_ref="request:1",
                            mode="editable", decision_ref="user:1")
        from runtime import changes
        guard_patch = patch.object(changes, 'LOCK_ROOT', self.base / 'authority-guards')
        guard_patch.start()
        self.addCleanup(guard_patch.stop)

    def fresh_scope(self):
        from tests.fixture_canonical_birth import fresh_canonical, save_test_acquisition
        fixture, self.acquisition, observation = fresh_canonical(self)
        self.base, self.scope = fixture.base, self.acquisition.resolution.scope
        self.test_acquisition = save_test_acquisition(self.base / 'test-acquisition.json', fixture, observation)

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("runtime.state"), "durable state implementation missing")
        return importlib.import_module("runtime.state")

    def start(self, **updates):
        return self.api().start_task(self.scope, {**self.request, **updates})

    def status(self, **updates):
        return self.api().task_status(self.scope, updates.get("client", "codex"), updates.get("session", "session"))

    def update(self, **updates):
        current = self.status()["task"]
        return self.api().update_task(self.scope, {key: self.request[key] for key in
                                      ("client", "session_id", "task_id", "scope_ref")} |
                                     dict(expected_revision=current["revision"], state=current["state"],
                                          reason="work", reference="user:1") | updates)

    def event(self, kind="PreToolUse", **updates):
        event = RuntimeEvent(kind, "codex", "session", self.scope.repository_root, "turn", "call", "apply_patch",
                             False, "fingerprint")
        return self.api().process_event(self.scope, replace(event, **updates))

    def runnable(self, **updates):
        return dict(id="next", kind="runnable", action="task", reason="Finish authorized step",
                    reference="user:1", continuation={"turn_id": "turn", "budget": 1}) | updates

    def arm(self):
        self.start()
        self.update(state='WORKING')
        self.event()
        self.update(conditions=[self.runnable()])

    def test_canonical_schema_three_start_restart_immutable_snapshot_and_physical_row(self):
        self.scope = canonical_scope(self.scope)
        first = self.start()
        api = self.api()
        self.assertEqual(first["task"]["pins"]["schema_version"], 3)
        self.assertEqual(first["task"]["accepted_identity"]["checkout_id"], self.scope.checkout_id)
        self.assertEqual(self.start(), first)
        with api._database(self.scope) as db:
            self.assertEqual(db.execute("pragma user_version").fetchone()[0], 3)
            physical = json.loads(db.execute("select physical_identity from checkouts").fetchone()[0])
            self.assertNotIn("client", physical)
            self.assertEqual(physical["checkout_id"], self.scope.checkout_id)
        self.assertEqual(api.SCHEMA_VERSION, 2)

    def test_canonical_scope_cannot_open_or_rewrite_legacy_database(self):
        self.start()
        path = self.scope.runtime.state_dir / "runtime.sqlite3"
        with closing(sqlite3.connect(path)) as db:
            db.execute("pragma user_version=2")
        before = path.read_bytes()
        scope = canonical_scope(self.scope)
        from runtime.startup import _canonical_plain, _canonical_freeze
        identity = _canonical_plain(scope.accepted_identity)
        identity["configuration"]["runtime"]["state_dir"] = str(self.scope.runtime.state_dir)
        import hashlib
        scope = replace(scope, runtime=RuntimeSettings(3, self.scope.runtime.state_dir),
                        accepted_identity=_canonical_freeze(identity), accepted_scope_digest=hashlib.sha256(json.dumps(
                            identity,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest())
        with self.assertRaises(self.api().StateError): self.api().start_task(scope, self.request)
        self.assertEqual(path.read_bytes(), before)

    def test_canonical_checkout_mismatch_rejects_start_and_event_without_generation_changes(self):
        self.scope = canonical_scope(self.scope)
        self.start()
        path = self.scope.runtime.state_dir / "runtime.sqlite3"
        with closing(sqlite3.connect(path)) as db, db:
            physical = json.loads(db.execute("select physical_identity from checkouts").fetchone()[0])
            physical["bindings"]["root"]["inode"] += 1
            db.execute("update checkouts set physical_identity=?", (json.dumps(physical),))
        before = path.read_bytes()
        with self.assertRaises(self.api().StateError): self.start(session_id="other")
        self.assertEqual(self.event().action, "deny")
        self.assertEqual(path.read_bytes(), before)

    def test_canonical_save_rejects_rebound_identity_and_missing_digest(self):
        self.scope = canonical_scope(self.scope)
        task = self.start()["task"]
        api = self.api()
        with api._database(self.scope, write=True) as db:
            del task["pins"]["accepted_scope_digest"]
            with self.assertRaises(api.StateError): api._save(db, api._scope_id(self.scope), task)
        self.assertIn("accepted_scope_digest", self.status()["task"]["pins"])


    def test_canonical_client_projections_share_physical_row_and_generation(self):
        original = self.scope
        self.scope = canonical_scope(original)
        self.start()
        self.event("PostToolUse")
        other = canonical_scope(original, client="claude-code")
        api = self.api()
        with api._database(self.scope, write=True) as db:
            row = api._checkout_row(db, self.scope)
            self.assertEqual(api._checkout_row(db, other, initialize=True), row)
            self.assertEqual(db.execute("select count(*) from checkouts").fetchone()[0], 1)
            self.assertEqual(row[4], 1)
        self.assertNotEqual(self.scope.accepted_scope_digest, other.accepted_scope_digest)
        self.assertEqual(api._scope_id(self.scope), api._scope_id(other))

    def test_canonical_wrong_client_event_cannot_write_generation(self):
        self.scope = canonical_scope(self.scope)
        self.start()
        path = self.scope.runtime.state_dir / "runtime.sqlite3"
        before = path.read_bytes()
        self.event("PostToolUse", client="claude-code")
        self.assertEqual(path.read_bytes(), before)

    def test_canonical_event_can_admit_only_the_exact_physical_checkout(self):
        self.scope = canonical_scope(self.scope)
        api = self.api()
        with api._database(self.scope, write=True, initialize=True): pass
        self.event("PostToolUse")
        with api._database(self.scope) as db:
            row = api._checkout_row(db, self.scope)
            self.assertEqual(row[4], 1)
            self.assertEqual(json.loads(row[5])["checkout_id"], self.scope.checkout_id)
            self.assertEqual(db.execute("select count(*) from tasks").fetchone()[0], 0)

    def test_canonical_changed_configuration_preserves_historical_snapshot_and_cannot_repin(self):
        import hashlib
        from runtime.startup import _canonical_plain, _canonical_freeze
        self.scope = canonical_scope(self.scope)
        self.start()
        path = self.scope.runtime.state_dir / "runtime.sqlite3"
        before = path.read_bytes()
        snapshot = _canonical_plain(self.scope.accepted_identity)
        snapshot["shared_policy"] = [dict(path=str(self.base / "guide"), sha256="a"*64)]
        digest = hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()
        changed = replace(self.scope, accepted_identity=_canonical_freeze(snapshot), accepted_scope_digest=digest)
        with self.assertRaises(self.api().StateError): self.api().task_status(changed, "codex", "session")
        self.assertEqual(path.read_bytes(), before)
        with self.api()._database(changed) as db:
            historical = self.api()._task(db, self.api()._scope_id(changed), "codex", "session", "task")
            self.assertEqual(historical["accepted_identity"]["shared_policy"], [])
        self.api().start_task(changed, self.request | dict(session_id="new-session", task_id="new-task"))
        with self.api()._database(changed) as db:
            self.assertEqual(self.api()._task(db, self.api()._scope_id(changed), "codex", "session", "task"), historical)

    def test_canonical_raw_foreign_snapshot_cannot_claim_another_state_authority(self):
        self.scope = canonical_scope(self.scope)
        task = self.start()["task"]
        task["accepted_identity"]["configuration"]["runtime"]["state_dir"] = str(self.base / "elsewhere")
        task["pins"]["accepted_scope_digest"] = self.api()._digest(task["accepted_identity"])
        with closing(sqlite3.connect(self.scope.runtime.state_dir / "runtime.sqlite3")) as db, db:
            db.execute("update tasks set data=?", (json.dumps(task),))
        with self.api()._database(self.scope) as db, self.assertRaises(self.api().StateError):
            self.api()._task(db, self.api()._scope_id(self.scope), "codex", "session", "task")


    def test_canonical_retirement_retains_exact_four_key_historical_pins(self):
        self.scope = canonical_scope(self.scope)
        self.test_historical_retirement_requires_exact_stopped_writer_proof_and_preserves_pins(canonical=True)


    def test_canonical_entry_rejects_mutable_scope_snapshots_before_initialization(self):
        from types import MappingProxyType
        from runtime.startup import _canonical_plain
        scope = canonical_scope(self.scope)
        path = scope.runtime.state_dir / 'runtime.sqlite3'
        before = path.read_bytes()
        for snapshot in (_canonical_plain(scope.accepted_identity), MappingProxyType(_canonical_plain(scope.accepted_identity))):
            with self.subTest(snapshot=type(snapshot).__name__), self.assertRaises(self.api().StateError):
                self.api().start_task(replace(scope, accepted_identity=snapshot), self.request)
        self.assertEqual(path.read_bytes(), before)

    def test_legacy_schema_one_status_remains_a_read_only_incompatibility_report(self):
        path = self.scope.runtime.state_dir / 'runtime.sqlite3'
        before = path.read_bytes()
        report = self.api().inspect_state(replace(self.scope, runtime=RuntimeSettings(1, self.scope.runtime.state_dir)))
        self.assertFalse(report['compatible'])
        self.assertIn('error', report)
        self.assertEqual(path.read_bytes(), before)

    def test_canonical_retirement_proof_cannot_be_stored_inside_bare_repository(self):
        import hashlib
        self.scope = canonical_scope(self.scope)
        task = self.start()['task']
        bare = self.base / 'bare'
        env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        subprocess.run(['git', '-c', 'init.templateDir=', '-c', 'core.hooksPath='+os.devnull,
                        'init', '--bare', '-q', str(bare)], check=True, env=env, capture_output=True)
        identity = {k: task[k] for k in ('client','session_id','task_id','scope_ref')}
        proof = dict(version=1, kind='stopped-writer-reconciliation', identity=identity,
            expected_revision=task['revision'], expected_pins=task['pins'], observed_turn_id=task['turn_id'],
            stopped=True, descendants_stopped=True, attempt_ids=[], observation_ref='fixture:stopped', coordinator_ref='fixture:checked')
        raw = json.dumps(proof).encode()
        path = bare / 'proof.json'; path.write_bytes(raw); path.chmod(0o400)
        with self.assertRaises(self.api().StateError):
            self.api().retire_task(self.scope, identity | dict(expected_revision=task['revision'], expected_pins=task['pins'],
                reason='Stopped', reference='fixture:retire', stop_proof=dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest())))
        self.assertEqual(self.status()['task'], task)


    def test_canonical_wrong_table_shape_is_rejected_without_rewriting(self):
        self.scope = canonical_scope(self.scope)
        self.start()
        path = self.scope.runtime.state_dir / "runtime.sqlite3"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("alter table checkouts drop column physical_identity")
        before = path.read_bytes()
        with self.assertRaises(self.api().StateError): self.start()
        self.assertEqual(path.read_bytes(), before)


    def test_lifecycle_schema_three_tables_and_indexes_leave_legacy_exact(self):
        self.scope = canonical_scope(self.scope)
        self.start()
        with self.api()._database(self.scope) as db:
            tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
            indexes = {r[0] for r in db.execute("select name from sqlite_master where type='index' and sql is not null")}
            self.assertTrue({'evidence_metadata','retired_identities'} <= tables)
            self.assertEqual(indexes, {'evidence_lifecycle','assignment_owner','retired_assignment_id'})
        legacy = replace(self.scope.runtime, schema_version=2, state_dir=self.base / 'legacy')
        old = ProjectScope('old', self.scope.repository_root, self.scope.git_common_dir, 'policy', legacy)
        with self.assertRaises(self.api().StateError):
            self.api().start_task(old, self.request)
        self.assertFalse(legacy.state_dir.exists())
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript(';'.join(self.api().TABLE_SQL.values()))
            self.assertEqual({r[0] for r in db.execute("select name from sqlite_master where type='table'")}, self.api().TABLES)
            self.assertEqual(db.execute("select count(*) from sqlite_master where type='index' and sql is not null").fetchone()[0], 0)

    def test_lifecycle_owner_time_is_once_and_caller_cannot_set_or_reset_age(self):
        self.scope = canonical_scope(self.scope)
        with patch('runtime.state._owner_time', return_value=100): first = self.start()['task']
        self.assertEqual(first['lifecycle'], dict(created_at=100,safe_closed_at=None,closure=None,compact=None))
        with patch('runtime.state._owner_time', side_effect=AssertionError('replay regenerated age')):
            self.assertEqual(self.start()['task'], first)
            self.update(state='WORKING')
            self.event('PostToolUse')
        task = self.status()['task']
        self.assertEqual(task['lifecycle']['created_at'],100)
        with self.api()._database(self.scope, write=True) as db:
            task['lifecycle']['created_at'] = 200
            with self.assertRaises(self.api().StateError): self.api()._save(db,self.api()._scope_id(self.scope),task)
        with self.assertRaises(self.api().StateError): self.start(lifecycle=dict(created_at=1))

    def test_lifecycle_unknown_old_task_remains_undated_and_protected(self):
        self.scope = canonical_scope(self.scope)
        task = self.start()['task']; del task['lifecycle']
        with closing(sqlite3.connect(self.scope.runtime.state_dir/'runtime.sqlite3')) as db,db:
            db.execute('update tasks set data=?',(json.dumps(task),))
        self.assertIsNone(self.api()._task_lifecycle(self.status()['task']))
        self.event('PostToolUse')
        self.assertNotIn('lifecycle',self.status()['task'])
        with self.api()._database(self.scope,write=True) as db:
            task['lifecycle']=dict(created_at=1,safe_closed_at=None,closure=None,compact=None)
            with self.assertRaises(self.api().StateError): self.api()._save(db,self.api()._scope_id(self.scope),task)

    def test_lifecycle_identity_reads_live_schema_three_task_from_exact_authority(self):
        from runtime import evidence
        self.scope=canonical_scope(self.scope)
        task=self.start()['task']; api=self.api()
        context=evidence._evidence_context(self.scope)
        reference=dict(authority=api._canonical_plain(context.authority_set[0]),scope_id=api._scope_id(self.scope),
            client=task['client'],session_id=task['session_id'],task_id=task['task_id'])
        with evidence._context_database(context) as db:
            actual=api._lifecycle_identity(db,'task',reference)
        self.assertEqual(dict(actual),dict(status='live',identity={k:reference[k] for k in ('scope_id','client','session_id','task_id')},
            pins_digest=api._digest(task['pins']),request_digest=None,created_at=task['lifecycle']['created_at']))
        with evidence._context_database(context) as db,self.assertRaises(api.StateError):
            api._lifecycle_identity(db,'task',reference | dict(task_id='missing'))
        wrong=reference | dict(authority=reference['authority'] | dict(directory_binding=dict(device=0,inode=0)))
        with evidence._context_database(context) as db,self.assertRaises(api.StateError):
            api._lifecycle_identity(db,'task',wrong)

    def test_lifecycle_identity_does_not_adopt_legacy_authority(self):
        from runtime import evidence
        api=self.api()
        with api._database(self.scope,write=True,initialize=True) as db:
            context=evidence._evidence_context(self.scope)
            reference=dict(authority=api._canonical_plain(context.authority_set[0]),scope_id='a'*64,
                client='codex',session_id='session',task_id='task')
            with self.assertRaises(api.StateError):
                api._lifecycle_identity(db,'task',reference)

    def test_lifecycle_identity_requires_original_task_age_and_rejects_live_marker_conflict(self):
        from runtime import evidence
        self.scope=canonical_scope(self.scope); task=self.start()['task']; api=self.api()
        context=evidence._evidence_context(self.scope)
        reference=dict(authority=api._canonical_plain(context.authority_set[0]),scope_id=api._scope_id(self.scope),
            client=task['client'],session_id=task['session_id'],task_id=task['task_id'])
        path=self.scope.runtime.state_dir/'runtime.sqlite3'
        with closing(sqlite3.connect(path)) as db,db:
            task.pop('lifecycle')
            db.execute('update tasks set data=?',(json.dumps(task),))
        with evidence._context_database(context) as db,self.assertRaises(api.StateError):
            api._lifecycle_identity(db,'task',reference)
        task['lifecycle']=dict(created_at='bad',safe_closed_at=None,closure=None,compact=None)
        with closing(sqlite3.connect(path)) as db,db:
            db.execute('update tasks set data=?',(json.dumps(task),))
        with evidence._context_database(context) as db,self.assertRaises(api.StateError):
            api._lifecycle_identity(db,'task',reference)
        task.pop('lifecycle')
        with closing(sqlite3.connect(path)) as db,db:
            db.execute('update tasks set data=?',(json.dumps(task),))
        owner={k:reference[k] for k in ('scope_id','client','session_id','task_id')}
        self.seed_marker('task',owner,request_digest=None)
        with evidence._context_database(context) as db,self.assertRaises(api.StateError):
            api._lifecycle_identity(db,'task',reference)

    def test_lifecycle_identity_reads_retired_task_age_and_expiry_without_live_body(self):
        from runtime import evidence
        self.scope=canonical_scope(self.scope); self.start(); api=self.api()
        context=evidence._evidence_context(self.scope)
        reference=dict(authority=api._canonical_plain(context.authority_set[0]),scope_id=api._scope_id(self.scope),
            client='codex',session_id='session',task_id='task')
        owner={k:reference[k] for k in ('scope_id','client','session_id','task_id')}
        marker=self.seed_marker('task',owner,request_digest=None,expired=True)
        marker['created_at']='unknown'
        with api._database(self.scope,write=True) as db:
            db.execute('update retired_identities set data=?',(json.dumps(marker),))
        with evidence._context_database(context) as db,self.assertRaises(api.StateError):
            api._lifecycle_identity(db,'task',reference)
        marker['created_at']=100
        with api._database(self.scope,write=True) as db:
            db.execute('update retired_identities set data=?',(json.dumps(marker),))
        with closing(sqlite3.connect(context.database)) as db,db:
            db.execute('delete from tasks where scope_id=? and client=? and session_id=? and task_id=?',
                (owner['scope_id'],owner['client'],owner['session_id'],owner['task_id']))
        with evidence._context_database(context) as db:
            actual=api._lifecycle_identity(db,'task',reference)
        self.assertEqual((actual['status'],actual['created_at'],actual['pins_digest'],actual['request_digest']),
            ('expired',marker['created_at'],marker['pins_digest'],None))
        marker.update(retired_at=marker['created_at']+1,outcome='completed',evidence_expired=False)
        with api._database(self.scope,write=True) as db:
            db.execute('update retired_identities set data=?',(json.dumps(marker),))
        with evidence._context_database(context) as db:
            retained=api._lifecycle_identity(db,'task',reference)
        self.assertEqual(retained['status'],'retired')

    def test_lifecycle_identity_uses_live_assignment_parser_then_marker_identity_only(self):
        from runtime import evidence, state
        from tests.test_delegation import CanonicalDelegationTests
        fixture=CanonicalDelegationTests(methodName='runTest')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        f=fixture.fixture
        result=f.api.reserve_coordinator(f.scope,f.operation_request())
        context=evidence._evidence_context(f.scope)
        with evidence._context_database(context) as db:
            record=f.api._ledger(f.scope,db)[0]
            reference=dict(authority=state._canonical_plain(context.authority_set[0]),id=record['id'],
                owner=record['owner'],assignment=record['data']['assignment'])
            live=state._lifecycle_identity(db,'assignment',reference)
        self.assertEqual((live['status'],live['identity'],live['pins_digest'],live['request_digest'],live['created_at']),
            ('live',dict(id=record['id'],owner=record['owner']),state._digest(record['body']['pins']),
             record['data']['request_digest'],record['data']['lifecycle']['created_at']))
        marker=dict(version=1,identity=record['owner'],assignment_id=record['id'],
            pins_digest=state._digest(record['body']['pins']),request_digest=record['data']['request_digest'],
            created_at=record['data']['lifecycle']['created_at'],retired_at=record['data']['lifecycle']['created_at']+1,
            outcome='unknown',evidence_expired=True)
        with f.api._reservation_write(f.scope) as db:
            db.execute('insert into retired_identities(kind,identity_digest,data,assignment_id) values(?,?,?,?)',
                ('assignment',state._retired_identity('assignment',record['owner']),json.dumps(marker),record['id']))
        with evidence._context_database(context) as db,self.assertRaises(state.StateError):
            state._lifecycle_identity(db,'assignment',reference)
        with f.api._reservation_write(f.scope) as db:
            db.execute('delete from assignments where id=?',(record['id'],))
        changed_pointer=dict(name='f'*32+'.json',digest='e'*64)
        with evidence._context_database(context) as db:
            expired=state._lifecycle_identity(db,'assignment',reference | dict(assignment=changed_pointer))
            with self.assertRaises(state.StateError):
                state._lifecycle_identity(db,'assignment',reference | dict(id='f'*32))
            with self.assertRaises(state.StateError):
                state._lifecycle_identity(db,'assignment',reference | dict(owner=record['owner'] | dict(dispatch_id='different')))
        self.assertEqual((expired['status'],expired['identity'],expired['pins_digest'],expired['request_digest'],expired['created_at']),
            ('expired',dict(id=record['id'],owner=record['owner']),marker['pins_digest'],marker['request_digest'],marker['created_at']))
        self.assertEqual(result['attempt_id'],record['id'])

    def seed_marker(self,kind,identity,*,request_digest=None,expired=False):
        api=self.api()
        data=dict(version=1,identity=identity,pins_digest='a'*64,request_digest=request_digest,
                  created_at=100,retired_at=200,outcome='unknown' if expired else 'completed',evidence_expired=expired)
        with api._database(self.scope,write=True) as db:
            db.execute('insert into retired_identities(kind,identity_digest,data) values(?,?,?)',(kind,api._retired_identity(kind,identity),json.dumps(data)))
        return data

    def test_lifecycle_retired_task_and_event_point_replays_cannot_recreate_rows(self):
        self.scope=canonical_scope(self.scope); api=self.api()
        with api._database(self.scope,write=True,initialize=True): pass
        owner=dict(scope_id=api._scope_id(self.scope),client='codex',session_id='session',task_id='task')
        self.seed_marker('task',owner,expired=True)
        with self.assertRaisesRegex(api.StateError,'[Rr]etired|expired'): self.start()
        turn={k:owner[k] for k in ('scope_id','client','session_id')} | dict(turn_id='turn')
        self.seed_marker('turn',turn)
        with api._database(self.scope) as db:
            generation=api._checkout_row(db,self.scope)[4]
        self.event('PostToolUse')
        with api._database(self.scope) as db:
            self.assertEqual(api._checkout_row(db,self.scope)[4],generation+1)
            self.assertEqual(db.execute('select count(*) from tasks').fetchone()[0],0)
            self.assertEqual(db.execute('select count(*) from receipts').fetchone()[0],0)

    def test_lifecycle_expired_marker_cannot_retain_a_passing_outcome(self):
        self.scope=canonical_scope(self.scope)
        with self.api()._database(self.scope,write=True,initialize=True): pass
        owner=dict(scope_id=self.api()._scope_id(self.scope),client='codex',session_id='session',task_id='task')
        marker=self.seed_marker('task',owner,expired=True)
        marker['outcome']='completed'
        with self.api()._database(self.scope,write=True) as db:
            db.execute('update retired_identities set data=?',(json.dumps(marker),))
        with self.api()._database(self.scope) as db,self.assertRaises(self.api().StateError): self.api()._retired(db,'task',owner)


    def test_lifecycle_indexes_are_exact_and_older_unshipped_schema_is_not_upgraded(self):
        self.scope=canonical_scope(self.scope); self.start()
        path=self.scope.runtime.state_dir/'runtime.sqlite3'
        with closing(sqlite3.connect(path)) as db,db:
            db.execute('drop index retired_assignment_id')
            db.execute("CREATE UNIQUE INDEX retired_assignment_id ON retired_identities(assignment_id) WHERE kind='assign ment'")
        before=path.read_bytes()
        with self.assertRaises(self.api().StateError): self.start()
        self.assertEqual(path.read_bytes(),before)

    def test_lifecycle_metadata_validates_columns_but_does_not_trust_physical_assertions(self):
        self.scope=canonical_scope(self.scope); self.start(); api=self.api()
        name='c'*32+'.json'; binding=dict(device=1,inode=10)
        value=dict(version=1,owner=dict(kind='project',project_id='project',task=None),created_at=100,
            **{'class':'compact'},bytes=7,status='live',closed_at=None,dependencies=[],targets=[],export=None,
            managed=dict(path=str(self.scope.runtime.state_dir/'evidence'/name),binding=binding,
                parents={str(self.scope.runtime.state_dir/'evidence'):binding},sha256='d'*64,mode=0o400,bytes=7))
        with api._database(self.scope,write=True) as db:
            db.execute('insert into evidence_metadata values(?,?,?,?,?,?,?,?,?)',
                (name,'d'*64,'project:project','compact',100,None,'live',7,json.dumps(value)))
        # Stored shape is valid even though there is no artifact; later evidence integration must establish ownership.
        self.assertFalse(Path(value['managed']['path']).exists())
        with api._database(self.scope) as db:
            self.assertEqual(api._metadata(db,name),value)
            self.assertIsNone(api._metadata(db,'e'*32+'.json'))
        value['bytes']=1
        value['managed']['bytes']=True
        with api._database(self.scope,write=True) as db:
            db.execute('update evidence_metadata set bytes=1,data=? where name=?',(json.dumps(value),name))
        with api._database(self.scope) as db,self.assertRaises(api.StateError): api._metadata(db,name)

    def test_lifecycle_caller_cannot_save_a_structurally_valid_synthetic_closure(self):
        self.scope=canonical_scope(self.scope); task=self.start()['task']; life=task['lifecycle']
        life['safe_closed_at']=life['created_at']+1
        life['closure']=dict(version=1,task_revision=task['revision'],dependency_digest='a'*64,
                             observed_at=life['safe_closed_at'],evidence=[])
        with self.api()._database(self.scope,write=True) as db,self.assertRaises(self.api().StateError):
            self.api()._save(db,self.api()._scope_id(self.scope),task)
        self.assertIsNone(self.status()['task']['lifecycle']['safe_closed_at'])


    def test_historical_retirement_requires_exact_stopped_writer_proof_and_preserves_pins(self, *, canonical=False):
        import hashlib
        self.start()
        old = self.status()["task"]
        self.start(task_id="new", replaces={"task_id": "task", "expected_revision": old["revision"]})
        with self.api()._database(self.scope) as db:
            historical = self.api()._task(db, self.api()._scope_id(self.scope), "codex", "session", "task")
        identity = {key: historical[key] for key in ("client", "session_id", "task_id", "scope_ref")}
        request = identity | dict(expected_revision=historical["revision"], expected_pins=historical["pins"],
                                 reason="Old writer stopped", reference="host:reconciliation")
        with self.assertRaises(self.api().StateError):
            self.api().retire_task(self.scope, request | dict(stop_proof={"stopped": True}))
        proof = dict(version=1, kind="stopped-writer-reconciliation", identity=identity,
                     expected_revision=historical["revision"], expected_pins=historical["pins"],
                     observed_turn_id=historical["turn_id"], stopped=True, descendants_stopped=True,
                     attempt_ids=[], observation_ref="host:observed-processes", coordinator_ref="coordinator:checked")
        raw = json.dumps(proof).encode()
        path = self.base / "stop-proof.json"
        path.write_bytes(raw)
        path.chmod(0o400)
        request["stop_proof"] = dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest())
        with self.assertRaises(self.api().StateError):
            self.api().retire_task(self.scope, request | dict(expected_revision=1))
        files = {str(p):p.read_bytes() for p in self.scope.runtime.state_dir.rglob('*') if p.is_file()}
        with patch.object(self.api(), "runtime_fingerprint", return_value="new-runtime"), \
                self.assertRaises(self.api().StateError):
            self.api().retire_task(self.scope, request)
        self.assertEqual({str(p):p.read_bytes() for p in self.scope.runtime.state_dir.rglob('*') if p.is_file()}, files)
        with self.api()._database(self.scope) as db:
            self.assertEqual(self.api()._task(db, self.api()._scope_id(self.scope), 'codex', 'session', 'task'), historical)
        result = self.api().retire_task(self.scope, request)
        self.assertEqual(result["task"]["state"], 'CANCELLED')
        self.assertEqual(result["task"]["pins"], historical["pins"])
        self.assertEqual(self.status()["task"]["task_id"], "new")
        with self.assertRaises(self.api().StateError):
            self.api().retire_task(self.scope, request)

    def test_schema_two_assignments_have_unique_dispatch_and_no_legacy_adoption(self):
        self.start()
        path = self.scope.runtime.state_dir / "runtime.sqlite3"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("insert into assignments values(?,?,?,?,?,?,?)",
                       ("one", "scope", "codex", "session", "task", "dispatch", "{}"))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("insert into assignments values(?,?,?,?,?,?,?)",
                           ("two", "scope", "codex", "session", "task", "dispatch", "{}"))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("insert into assignments values(?,?,?,?,?,?,?)",
                           ("one", "other", "codex", "session", "task", "dispatch", "{}"))
            db.execute("drop table assignments")
            db.execute("pragma user_version=1")
        original = path.read_bytes()
        with self.assertRaises(self.api().StateError):
            self.status()
        with self.assertRaises(self.api().StateError):
            self.start()
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(self.api().inspect_state(self.scope)["compatible"])

    def test_same_columns_without_assignment_constraints_are_rejected(self):
        self.start()
        path = self.scope.runtime.state_dir / "runtime.sqlite3"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("drop table assignments")
            db.execute("create table assignments(id TEXT,scope_id TEXT,client TEXT,session_id TEXT,"
                       "task_id TEXT,dispatch_id TEXT,data TEXT)")
        with self.assertRaises(self.api().StateError):
            self.status()

    def test_start_restart_idempotence_and_pins(self):
        first = self.start()
        self.assertEqual(first["task"]["state"], 'TRIAGE')
        self.assertEqual(first["task"]["revision"], 1)
        self.assertEqual(first, self.start())
        result = subprocess.run([sys.executable, "-B", "-c",
            "from runtime.state import *; from runtime.startup import *; from pathlib import Path; import json; "
            f"from tests.fixture_canonical_birth import acquire_test_scope; s=acquire_test_scope({str(self.test_acquisition)!r}); "
            "print(json.dumps(task_status(s,'codex','session')))"], cwd=PACKAGE, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["task"], first["task"])
        self.assertEqual(set(first["task"]["pins"]), {"policy_digest", "runtime_digest", "schema_version", "accepted_scope_digest"})

    def test_explicit_rebinding_preserves_old_task_and_revokes_continuation(self):
        self.arm()
        old = self.status()["task"]
        with self.assertRaises(self.api().StateError):
            self.start(task_id="other")
        new = self.start(task_id="other", replaces={"task_id": "task", "expected_revision": old["revision"]})
        self.assertEqual(new["task"]["task_id"], "other")
        with closing(sqlite3.connect(self.scope.runtime.state_dir / "runtime.sqlite3")) as db, db:
            stored = json.loads(db.execute("select data from tasks where task_id='task'").fetchone()[0])
        self.assertEqual(stored["revision"], old["revision"] + 1)
        self.assertTrue(stored["conditions"][0]["continuation"]["revoked"])
        self.assertEqual(self.event("Interrupt").action, "context")
        self.assertEqual(self.status()["task"]["state"], 'TRIAGE')

    def test_cas_transitions_terminal_cancellation_and_immutable_mode(self):
        self.start()
        for updates in ({"state": 'DONE'}, {"state": 'NOT A STATUS'}, {"expected_revision": 0}, {"mode": "read-only"}):
            with self.subTest(updates=updates), self.assertRaises(self.api().StateError):
                self.update(**updates)
        self.update(state='WORKING')
        self.update(state='REVIEW')
        self.update(state='WORKING')
        self.update(state='BLOCKED')
        with self.assertRaises(self.api().StateError):
            self.update(state='WORKING')
        self.update(state='WORKING', resolution_ref="user:resolved")
        self.update(state='CANCELLED')
        with self.assertRaises(self.api().StateError):
            self.update(state='WORKING')

    def test_conditions_need_explicit_resolution_and_no_approval_is_inferred(self):
        self.start()
        self.update(state='WORKING', conditions=[dict(id="decision", kind="decision", action="task",
                                                     reason="Choose", reference="agent:claims-user-approved")])
        self.assertEqual(self.event().action, "deny")
        self.update(conditions=[])
        self.assertEqual(self.event().action, "deny")
        self.update(resolutions=[{"id": "decision", "reference": "user:choice"}])
        self.assertEqual(self.event().action, "pass")

    def test_malformed_controls_are_rejected_without_initialization(self):
        for updates in ({"client": "other"}, {"task_id": "x" * 129}, {"session_id": []},
                        {"mode": "auto"}, {"decision_ref": "bad\nvalue"}, {"surprise": True}):
            with self.subTest(updates=updates), self.assertRaises(self.api().StateError):
                self.start(**updates)
        with self.api()._database(self.scope) as db:
            self.assertEqual(db.execute('select count(*) from tasks').fetchone()[0], 0)

    def test_task_identity_includes_checkout_common_project_client_session(self):
        self.start()
        for changes in ({"repository_root": self.base / "other"}, {"git_common_dir": self.base / "common"},
                        {"project_id": "other"}):
            other = replace(self.scope, **changes)
            with self.assertRaises(self.api().StateError):
                self.api().task_status(other, "codex", "session")
        self.assertIsNone(self.status(session="different")["task"])

    def test_hooks_status_and_inspection_do_not_initialize(self):
        api = self.api()
        self.assertTrue(api.inspect_state(self.scope)["present"])
        original_inode=(self.scope.runtime.state_dir/'runtime.sqlite3').stat().st_ino
        self.assertIsNone(self.status()["task"])
        self.assertEqual(self.event().action, "deny")
        self.assertEqual(self.event("Stop").action, "pass")
        self.assertEqual((self.scope.runtime.state_dir/'runtime.sqlite3').stat().st_ino,original_inode)
        with api._database(self.scope) as db:self.assertEqual(db.execute('select count(*) from tasks').fetchone()[0],0)
        with self.assertRaises(api.StateError):
            api.start_task(replace(self.scope, runtime=None), self.request)
        self.assertEqual(api.process_event(replace(self.scope, runtime=None), RuntimeEvent(
            "PreToolUse", "codex", "session", self.scope.repository_root, tool_name="apply_patch")).action, "deny")
        self.assertFalse(api.inspect_state(replace(self.scope,runtime=None))["compatible"])

    def test_private_directory_database_sidecars_and_corrupt_state(self):
        api = self.api()
        directory = self.scope.runtime.state_dir
        directory.chmod(0o755)
        with self.assertRaises(api.StateError):
            self.start()
        directory.chmod(0o700)
        path = directory / "runtime.sqlite3"
        path.write_text("not our database")
        path.chmod(0o600)
        with self.assertRaises(api.StateError):
            self.start()
        self.assertEqual(path.read_text(), "not our database")
        self.fresh_scope()
        directory=self.scope.runtime.state_dir
        path=directory/'runtime.sqlite3'
        self.start()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        sidecar = directory / "runtime.sqlite3-journal"
        sidecar.symlink_to(path)
        with self.assertRaises(api.StateError):
            self.status()
        sidecar.unlink()
        path.chmod(0o644)
        self.assertEqual(self.event().action, "deny")
        self.assertEqual(self.event("Stop").action, "pass")

    def test_arbitrary_sqlite_schema_and_schema_version_are_not_adopted(self):
        directory = self.scope.runtime.state_dir
        path = directory / "runtime.sqlite3"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("create table private(value)")
        path.chmod(0o600)
        with self.assertRaises(self.api().StateError):
            self.start()
        self.fresh_scope()
        path=self.scope.runtime.state_dir/'runtime.sqlite3'
        self.start()
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("pragma user_version=99")
        with self.assertRaises(self.api().StateError):
            self.status()
        self.assertFalse(self.api().inspect_state(self.scope)["compatible"])

    def test_policy_runtime_pin_mismatch_and_lock_fail_closed(self):
        self.start()
        self.update(state='WORKING')
        changed = replace(self.scope, policy_digest="changed")
        self.assertEqual(self.api().process_event(changed, RuntimeEvent("PreToolUse", "codex", "session",
                         self.scope.repository_root, tool_name="apply_patch")).action, "deny")
        with patch("runtime.state.runtime_fingerprint", return_value="changed"):
            with self.assertRaises(self.api().StateError):
                self.update()
        with closing(sqlite3.connect(self.scope.runtime.state_dir / "runtime.sqlite3")) as db, db:
            db.execute("begin exclusive")
            self.assertEqual(self.event().action, "deny")
            self.assertEqual(self.event("Stop").action, "pass")

    def test_patch_guard_and_scoped_blockers(self):
        self.assertEqual(self.event().action, "deny")
        self.start(mode="read-only")
        self.update(state='WORKING')
        self.assertEqual(self.event().action, "deny")
        self.assertEqual(self.event(tool_name="Bash").action, "pass")
        self.start(session_id="editable")
        other = RuntimeEvent("PreToolUse", "codex", "editable", self.scope.repository_root, tool_name="apply_patch")
        self.api().update_task(self.scope, dict(client="codex", session_id="editable", task_id="task",
            scope_ref="request:1", expected_revision=1, state='WORKING', reason="work", reference="user:1"))
        self.assertEqual(self.api().process_event(self.scope, other).action, "pass")
        self.update(conditions=[dict(id="patch", kind="blocker", action="patch", reason="Check", reference="ref")])
        self.assertEqual(self.api().process_event(self.scope, other).action, "pass")

    def test_claude_file_tools_use_the_shared_bound_task_guard(self):
        self.scope = canonical_scope(self.scope, client="claude-code")
        api = self.api()
        event = lambda name: RuntimeEvent("PreToolUse", "claude-code", "claude-session",
                                          self.scope.repository_root, tool_name=name)
        for name in ("Edit", "Write", "NotebookEdit"):
            with self.subTest(tool=name):
                self.assertEqual(api.process_event(self.scope, event(name)).action, "deny")

        request = self.request | dict(client="claude-code", session_id="claude-session")
        api.start_task(self.scope, request)
        api.update_task(self.scope, {key: request[key] for key in ("client", "session_id", "task_id", "scope_ref")} |
                        dict(expected_revision=1, state='WORKING', reason="work", reference="user:1"))
        for name in ("Edit", "Write", "NotebookEdit"):
            with self.subTest(tool=name):
                self.assertEqual(api.process_event(self.scope, event(name)).action, "pass")

        api.update_task(self.scope, {key: request[key] for key in ("client", "session_id", "task_id", "scope_ref")} |
                        dict(expected_revision=2, state='WORKING', reason="work", reference="user:1",
                             conditions=[dict(id="block", kind="blocker", action="patch", reason="Check", reference="ref")]))
        for name in ("Edit", "Write", "NotebookEdit"):
            with self.subTest(tool=name, blocked=True):
                self.assertEqual(api.process_event(self.scope, event(name)).action, "deny")
        self.assertEqual(api.process_event(self.scope, event("Bash")).action, "pass")
        self.assertEqual(api.process_event(self.scope, event("mcp__demo__write")).action, "pass")

    def test_claude_status_does_not_claim_stored_ready_as_verified(self):
        self.scope = canonical_scope(self.scope, client="claude-code")
        api = self.api()
        request = self.request | dict(client="claude-code", session_id="claude-session")
        api.start_task(self.scope, request)
        with api._database(self.scope, write=True) as db:
            scope_id = api._scope_id(self.scope)
            raw = db.execute("select data from tasks where scope_id=? and client=? and session_id=? and task_id=?",
                             (scope_id, "claude-code", "claude-session", "task")).fetchone()[0]
            task = json.loads(raw)
            task["state"] = 'DONE'
            db.execute("update tasks set data=? where scope_id=? and client=? and session_id=? and task_id=?",
                       (json.dumps(task, separators=(",", ":")), scope_id, "claude-code", "claude-session", "task"))
        with self.assertRaisesRegex(api.StateError, "Claude Code task readiness is unverified"):
            api.task_status(self.scope, "claude-code", "claude-session")

    def test_claude_task_controls_require_exact_canonical_client_scope(self):
        api = self.api()
        request = self.request | dict(client="claude-code", session_id="claude-session")
        with self.assertRaisesRegex(api.StateError, "accepted canonical client scope"):
            api.start_task(self.scope, request)
        with self.assertRaisesRegex(api.StateError, "accepted canonical client scope"):
            api.task_status(self.scope, "claude-code", "claude-session")
        event = RuntimeEvent("PreToolUse", "claude-code", "claude-session", self.scope.repository_root,
                            tool_name="Edit")
        self.assertEqual(api.process_event(self.scope, event).action, "deny")

    def test_claude_scope_does_not_enable_retirement_evidence_or_delegation_apis(self):
        self.scope = canonical_scope(self.scope, client="claude-code")
        api = self.api()
        from runtime import evidence, delegation
        identity = self.request | dict(client="claude-code", session_id="claude-session")
        task = api.start_task(self.scope, identity)["task"]
        retirement = {key: task[key] for key in ("client", "session_id", "task_id", "scope_ref")}
        retirement.update(expected_revision=task["revision"], expected_pins=task["pins"], reason="Stopped",
                          reference="fixture:stop", stop_proof={})
        with self.assertRaisesRegex(api.StateError, "Unsupported task client"):
            api.retire_task(self.scope, retirement)
        evidence_request = {key: identity[key] for key in ("client", "session_id", "task_id", "scope_ref")}
        evidence_request.update(expected_revision=1, plan={})
        with self.assertRaisesRegex(api.StateError, "Unsupported task client"):
            evidence.plan_evidence(self.scope, evidence_request)
        with self.assertRaisesRegex(api.StateError, "Unsupported task client"):
            delegation.plan_delegation(self.scope, evidence_request | {"plan": {}})

    def test_shared_scans_decode_canonical_claude_task_without_enabling_evidence_apis(self):
        api = self.api()
        codex_scope = canonical_scope(self.scope, client="codex")
        claude_scope = canonical_scope(self.scope, client="claude-code")
        api.start_task(codex_scope, self.request)
        claude_request = self.request | dict(client="claude-code", session_id="claude-session")
        api.start_task(claude_scope, claude_request)

        report = api.inspect_state(claude_scope)
        self.assertNotIn("error", report)
        self.assertEqual(len(report["tasks"]), 2)

        from runtime import delegation, evidence
        with api._database(codex_scope) as db:
            delegation._resources_available(codex_scope, db, [], [])
        evidence_request = {key: claude_request[key] for key in
                            ("client", "session_id", "task_id", "scope_ref")}
        evidence_request.update(expected_revision=1, purpose="completion")
        with self.assertRaisesRegex(api.StateError, "Unsupported task client"):
            evidence.verify_completion(claude_scope, evidence_request)

    def test_canonical_claude_task_replacement_rebinds_only_with_exact_revision(self):
        self.scope = canonical_scope(self.scope, client="claude-code")
        api = self.api()
        request = self.request | dict(client="claude-code", session_id="claude-session")
        first = api.start_task(self.scope, request)["task"]
        replacement = request | dict(task_id="task-next", replaces={"task_id": first["task_id"],
                                                                       "expected_revision": first["revision"]})
        result = api.start_task(self.scope, replacement)
        self.assertEqual(result["task"]["task_id"], "task-next")
        self.assertEqual(api.task_status(self.scope, "claude-code", "claude-session")["task"]["task_id"],
                         "task-next")

    def test_receipts_deduplicate_conflicts_missing_identity_and_late_events(self):
        self.start()
        self.event("PostToolUse", tool_name="unknown")
        self.assertEqual(self.status()["generation"], 1)
        self.event("PostToolUse", tool_name="unknown")
        self.assertEqual(self.status()["generation"], 1)
        result = self.event("PostToolUse", receipt_fingerprint="conflict")
        self.assertEqual(result.action, "context")
        self.assertEqual(self.status()["generation"], 2)
        for _ in range(2):
            self.event("PostToolUse", turn_id=None)
        self.assertEqual(self.status()["generation"], 4)
        other = replace(self.scope, repository_root=self.base / "other")
        with self.assertRaises(self.api().StateError):
            self.api().task_status(other, "codex", "session")

    def test_continuation_requires_observation_and_never_rearms(self):
        self.start()
        self.update(state='WORKING')
        with self.assertRaises(self.api().StateError):
            self.update(conditions=[self.runnable()])
        self.event()
        self.update(conditions=[self.runnable()])
        self.assertEqual(self.event("Stop").action, "continue")
        self.assertEqual(self.event("Stop").action, "pass")
        self.update(conditions=[self.runnable(reason="Edited")])
        self.assertEqual(self.event("Stop").action, "pass")
        self.assertEqual(self.status()["task"]["state"], 'WORKING')

    def test_stop_suppression_for_uncertainty_budget_state_and_blockers(self):
        self.arm()
        self.assertEqual(self.event("Stop", stop_hook_active=None).action, "pass")
        self.assertEqual(self.event("Stop", stop_hook_active=True).action, "pass")
        self.update(conditions=[self.runnable(continuation={"turn_id": "turn", "budget": 0})])
        self.assertEqual(self.event("Stop").action, "pass")
        self.assertEqual(self.event("Stop", turn_id=None).action, "pass")
        self.assertEqual(self.event("Stop", turn_id="unobserved").action, "pass")
        self.event(turn_id="new", tool_call_id="new-call")
        self.assertEqual(self.event("Stop").action, "pass")
        self.assertEqual(self.status()["task"]["conditions"][0]["continuation"]["revoked"], True)

    def test_interruption_pauses_only_correlated_task_and_cannot_continue(self):
        self.arm()
        self.assertEqual(self.event("Interrupt", turn_id="unknown").action, "context")
        self.assertEqual(self.status()["task"]["state"], 'WORKING')
        self.assertEqual(self.event("Interrupt").action, "pass")
        self.assertEqual(self.status()["task"]["state"], 'PAUSED')
        self.assertEqual(self.event("Stop").action, "pass")
        self.update(state='WORKING', resolution_ref="user:resume")
        self.assertEqual(self.event("Stop").action, "pass")

    def test_late_old_turns_do_not_pause_or_reassign_new_binding(self):
        self.arm()
        revision = self.status()["task"]["revision"]
        self.start(task_id="new-task", replaces=dict(task_id="task", expected_revision=revision))
        self.request["task_id"] = "new-task"
        self.update(state='WORKING')
        self.event(turn_id="new-turn", tool_call_id="new-call")
        self.update(conditions=[self.runnable(continuation={"turn_id": "new-turn", "budget": 1})])
        before = self.status()["task"]
        self.event("PostToolUse")
        self.event()
        self.assertEqual(self.event("Interrupt").action, "context")
        self.assertEqual(self.event("Stop").action, "pass")
        self.assertEqual(self.status()["task"], before)
        self.assertEqual(self.event("Stop", turn_id="new-turn").action, "continue")
        self.assertEqual(self.status()["generation"], 1)

    def test_out_of_order_observations_keep_latest_turn_and_payloads_are_not_stored(self):
        self.arm()
        self.event(turn_id="second", tool_call_id="second-call")
        self.event()
        self.assertEqual(self.status()["task"]["turn_id"], "second")
        self.assertEqual(self.event("Stop").action, "pass")
        from runtime.adapters.codex import normalize_runtime
        for response in ({"exit_code": 0}, {"exit_code": 2, "stderr": "PRIVATE_SENTINEL"}):
            event = normalize_runtime(dict(hook_event_name="PostToolUse", session_id="session",
                cwd=str(self.scope.repository_root), turn_id="second", tool_use_id="result-" + str(response["exit_code"]),
                tool_name="Bash", tool_input={"command": "PRIVATE_SENTINEL"}, tool_response=response))
            self.api().process_event(self.scope, event)
        self.assertEqual(self.status()["generation"], 2)
        self.assertEqual(self.status()["task"]["state"], 'WORKING')
        for path in self.scope.runtime.state_dir.rglob("*"):
            if path.is_file(): self.assertNotIn(b"PRIVATE_SENTINEL", path.read_bytes())

    def test_cancelled_waiting_blocked_and_decisions_suppress_stop(self):
        for state in ('BLOCKED', 'WAITING', 'CANCELLED'):
            with self.subTest(state=state):
                self.arm()
                self.update(state=state)
                self.assertEqual(self.event("Stop").action, "pass")
                self.assertEqual(self.status()["task"]["state"], state)
                # A separate fixture preserves terminal task identity and consumed history.
                self.fresh_scope()
        self.arm()
        for kind in ("decision", "blocker"):
            self.update(conditions=[dict(id=kind, kind=kind, action="task", reason="Waiting", reference="external:1")])
            self.assertEqual(self.event("Stop").action, "pass")
            self.update(resolutions=[dict(id=kind, reference="user:resolution")])
        self.assertEqual(self.event("Stop").action, "continue")

    def test_turn_uncertainty_and_conflicting_identity_revoke_continuation(self):
        self.arm()
        self.event("PostToolUse")
        self.event("PostToolUse", receipt_fingerprint="conflicting")
        self.assertTrue(self.status()["task"]["turn_uncertain"])
        self.assertEqual(self.event("Stop").action, "pass")
        self.assertTrue(self.status()["task"]["conditions"][0]["continuation"]["revoked"])

    def test_continuation_budget_cannot_be_increased_on_edits(self):
        self.arm()
        self.update(conditions=[self.runnable(continuation={"turn_id": "turn", "budget": 0})])
        with self.assertRaises(self.api().StateError):
            self.update(conditions=[self.runnable()])
        self.assertEqual(self.event("Stop").action, "pass")

    def test_symlink_paths_missing_database_and_read_only_status_are_safe(self):
        self.start()
        directory = self.scope.runtime.state_dir
        database = directory / "runtime.sqlite3"
        before = database.read_bytes()
        self.status()
        self.api().inspect_state(self.scope)
        self.assertEqual(database.read_bytes(), before)
        target = self.base / "database-copy"
        database.rename(target)
        database.symlink_to(target)
        self.assertEqual(self.event().action, "deny")
        with self.assertRaises(self.api().StateError):
            self.start()
        database.unlink()
        self.assertEqual(self.event("Stop").action, "pass")
        self.assertFalse(database.exists())
        from runtime import workstreams
        workstreams._path(self.scope).unlink()
        directory.rename(self.base/'retained-original-authority')
        directory.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(self.api().StateError):
            self.start()
        self.assertFalse((self.base / "runtime.sqlite3").exists())

    def test_corrupt_typed_task_record_fails_closed(self):
        self.arm()
        path = self.scope.runtime.state_dir / "runtime.sqlite3"
        original = self.status()["task"]
        for value in ({**original, "turn_uncertain": None},
                      {**original, "revision": -1},
                      {**original, "conditions": [dict(original["conditions"][0], status="unknown")]},
                      {**original, "turn_id": []}):
            with self.subTest(value=value):
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute("update tasks set data=?", (json.dumps(value),))
                with self.assertRaises(self.api().StateError):
                    self.status()
                self.assertEqual(self.event().action, "deny")
                self.assertEqual(self.event("Stop").action, "pass")

    def test_update_rejects_malformed_conditions_and_identity_fields(self):
        self.arm()
        for updates in ({"conditions": [dict(self.runnable(), id=[])]},
                        {"conditions": [dict(self.runnable(), kind=[])]},
                        {"conditions": [dict(self.runnable(), continuation={"turn_id": "turn", "budget": True})]},
                        {"conditions": [dict(self.runnable(), unknown=True)]},
                        {"conditions": [self.runnable(), self.runnable()]},
                        {"resolutions": [{"id": "missing", "reference": "ref"}]},
                        {"state": []}, {"expected_revision": True}, {"scope_ref": "different"}):
            with self.subTest(updates=updates), self.assertRaises(self.api().StateError):
                self.update(**updates)

    def test_unseen_new_turn_post_revokes_old_request_without_binding_or_pausing(self):
        self.arm()
        self.event("PostToolUse", turn_id="unseen-new-turn", tool_call_id="new-result")
        task = self.status()["task"]
        self.assertEqual(task["state"], 'WORKING')
        self.assertEqual(task["turn_id"], "turn")
        self.assertTrue(task["turn_uncertain"])
        self.assertEqual(self.event("Stop").action, "pass")
        self.assertTrue(task["conditions"][0]["continuation"]["revoked"])
        # It cannot later become an observed bound turn from completion alone.
        self.event(turn_id="unseen-new-turn", tool_call_id="followup")
        self.assertEqual(self.status()["task"]["turn_id"], "turn")

    def test_unknown_or_missing_stop_persistently_suppresses_old_allowance(self):
        for observed in ("unseen-stop-turn", None):
            with self.subTest(observed=observed):
                self.fresh_scope()
                self.arm()
                self.assertEqual(self.event("Stop", turn_id=observed).action, "pass")
                task = self.status()["task"]
                self.assertEqual(task["state"], 'WORKING')
                self.assertEqual(task["turn_id"], "turn")
                self.assertTrue(task["turn_uncertain"])
                self.assertTrue(task["conditions"][0]["continuation"]["revoked"])
                self.assertEqual(self.event("Stop").action, "pass")
                if observed:
                    self.event(turn_id=observed, tool_call_id="followup")
                    self.assertEqual(self.status()["task"]["turn_id"], "turn")
                with closing(sqlite3.connect(self.scope.runtime.state_dir / "runtime.sqlite3")) as db:
                    turns = db.execute("select turn_id,task_id from turns order by turn_id").fetchall()
                expected_turns = [("turn", "task")]
                if observed:
                    expected_turns.append((observed, None))
                self.assertEqual(turns, sorted(expected_turns))

    def test_conflicted_receipt_remains_poisoned_when_original_fingerprint_returns_after_restart(self):
        self.start()
        self.event("PostToolUse", receipt_fingerprint="A")
        self.assertEqual(self.status()["generation"], 1)
        self.event("PostToolUse", receipt_fingerprint="A")
        self.assertEqual(self.status()["generation"], 1)
        self.assertEqual(self.event("PostToolUse", receipt_fingerprint="B").action, "context")
        self.assertEqual(self.status()["generation"], 2)
        code = ("from pathlib import Path; from runtime.startup import *; from runtime.state import *; "
                "from runtime.engine import *; import json; "
                f"from tests.fixture_canonical_birth import acquire_test_scope; s=acquire_test_scope({str(self.test_acquisition)!r}); "
                "r=process_event(s,RuntimeEvent('PostToolUse','codex','session',s.repository_root,'turn','call',"
                "'apply_patch',False,'A')); print(json.dumps({'action':r.action,"
                "'generation':task_status(s,'codex','session')['generation']}))")
        result = subprocess.run([sys.executable, "-B", "-c", code], cwd=PACKAGE,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), dict(action="context", generation=3))
        self.assertEqual(self.event("PostToolUse", receipt_fingerprint="A").action, "context")
        self.assertEqual(self.status()["generation"], 4)
        with closing(sqlite3.connect(self.scope.runtime.state_dir / "runtime.sqlite3")) as db:
            self.assertEqual(db.execute("select fingerprint,conflicted from receipts").fetchall(), [("A", 1)])

    def test_unknown_interrupt_turn_is_unbound_and_cannot_be_guessed_by_later_pre(self):
        self.arm()
        original = self.status()["task"]
        self.assertEqual(self.event("Interrupt", turn_id="unknown-interrupt-turn").action, "context")
        self.assertEqual(self.status()["task"], original)
        self.event(turn_id="unknown-interrupt-turn", tool_call_id="later-pre")
        self.assertEqual(self.status()["task"], original)
        self.assertEqual(self.event("Stop").action, "continue")
        with closing(sqlite3.connect(self.scope.runtime.state_dir / "runtime.sqlite3")) as db:
            self.assertEqual(db.execute("select task_id from turns where turn_id='unknown-interrupt-turn'").fetchone(), (None,))

    def test_per_turn_continuation_cap_survives_distinct_condition_ids_and_edits(self):
        self.arm()
        self.update(conditions=[self.runnable(id="second")])
        self.assertEqual(self.event("Stop").action, "continue")
        self.assertEqual(self.event("Stop").action, "pass")
        self.update(conditions=[self.runnable(id="third")])
        self.assertEqual(self.event("Stop").action, "pass")
        self.event(turn_id="second-turn", tool_call_id="second-turn-call")
        self.update(conditions=[self.runnable(id="new-turn-step", continuation={"turn_id": "second-turn", "budget": 1})])
        self.assertEqual(self.event("Stop", turn_id="second-turn").action, "continue")
        self.assertEqual(self.event("Stop", turn_id="second-turn").action, "pass")

    def test_concurrent_separate_process_stop_consumes_once(self):
        self.arm()
        self.update(conditions=[self.runnable(id="second"), self.runnable(id="third")])
        code = ("from pathlib import Path; from runtime.startup import *; from runtime.state import *; "
                "from runtime.engine import *; import json; "
                f"from tests.fixture_canonical_birth import acquire_test_scope; s=acquire_test_scope({str(self.test_acquisition)!r}); "
                "print(process_event(s, RuntimeEvent('Stop','codex','session',s.repository_root,'turn',"
                "stop_hook_active=False)).action)")
        processes = [subprocess.Popen([sys.executable, "-B", "-c", code], cwd=PACKAGE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
        results = [process.communicate(timeout=5) for process in processes]
        self.assertTrue(all(process.returncode == 0 for process in processes), results)
        self.assertEqual(sum(stdout.strip() == b"continue" for stdout, _ in results), 1, results)


    def test_actual_startup_only_acquisition_remains_non_enforcing(self):
        from tests.test_runtime import RuntimeTests
        from runtime import startup
        from types import SimpleNamespace
        fixture=RuntimeTests('runTest');self.addCleanup(fixture.doCleanups);fixture.setUp()
        fixture.enroll()
        acquired=startup.acquire_project_entry(SimpleNamespace(client='codex',cwd=fixture.repo),PACKAGE)
        scope=startup._entry_scope(acquired)
        self.assertIsInstance(scope,startup.ProjectScope)
        self.assertIsNone(scope.runtime)
        self.assertEqual(self.api().process_event(scope,RuntimeEvent('PreToolUse','codex','session',fixture.repo,tool_name='apply_patch')).action,'pass')
        self.assertTrue(self.api().inspect_state(scope)['compatible'])

    def test_real_members_and_projects_isolate_task_identity(self):
        from tests.fixture_canonical_birth import CanonicalBirthFixture, fresh_canonical
        from runtime import state
        owner=CanonicalBirthFixture('runTest');self.addCleanup(owner.doCleanups)
        fixture,acquired,_=owner.accepted_birth(peer=True)
        members=[a.resolution.scope for a in fixture.member_acquisitions.values()]
        self.assertEqual(len(members),2)
        self.assertEqual(members[0].project_id,members[1].project_id)
        self.assertNotEqual(members[0].repository_id,members[1].repository_id)
        self.assertNotEqual(members[0].git_common_dir,members[1].git_common_dir)
        self.assertEqual(members[0].runtime.state_dir,members[1].runtime.state_dir)
        state.start_task(members[0],self.request)
        self.assertIsNone(state.task_status(members[1],'codex','session')['task'])
        state.start_task(members[1],self.request)
        self.assertNotEqual(state._scope_id(members[0]),state._scope_id(members[1]))
        _,other,_=fresh_canonical(self)
        self.assertNotEqual(other.resolution.scope.project_id,members[0].project_id)
        self.assertIsNone(state.task_status(other.resolution.scope,'codex','session')['task'])


if __name__ == "__main__":
    unittest.main()
