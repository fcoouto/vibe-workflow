"""Delegation dispatcher CLI boundaries use disposable enrollments."""
from tests.fixture_canonical_birth import fixture_artifact

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import unittest
from runtime import delegation, state

from runtime import startup


PACKAGE = Path(__file__).resolve().parents[1]


class DelegationDispatcherTests(unittest.TestCase):
    def setUp(self):
        from tests.test_runtime import RuntimeTests
        self.fixture = RuntimeTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.canonical()
        self.base, self.repo = self.fixture.base, self.fixture.repo
        self.nested = self.repo / "nested"
        self.nested.mkdir()
        self.cache = dict(client_surface="codex-cli", applicability=dict(
            client_version="0.163.0", capability_fingerprint="a" * 64))

    def canonical(self, *, linked=False):
        self.fixture._canonical_linked=linked
        self.fixture.canonical()
        self.base,self.repo=self.fixture.base,self.fixture.repo
        self.nested=self.repo/'nested';self.nested.mkdir(exist_ok=True)
        return self.fixture.scope

    def cli(self, parts, request=None, *, raw=None, flags=(), family='delegation', native=None, cwd=None, env_updates=None):
        package = PACKAGE
        bootstrap = f"""
import runpy, sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, {str(package)!r})
from runtime import changes, startup
import json
observation=json.loads(Path({str((getattr(self.fixture,'test_acquisition',None) or self.fixture.fixture.test_acquisition))!r}).read_bytes())['observation']
original_acquire=startup.acquire_project_entry
startup.acquire_project_entry=lambda *args,**kwargs:original_acquire(*args,**(kwargs|dict(qualifier=lambda record,facts:observation)))
from runtime.adapters import codex
changes.LOCK_ROOT = Path({str(self.base / 'authority-guards')!r})
native = {native or {}!r}
with ExitStack() as mocks:
    for name in ('discover_native_workers', 'observe_native_worker',
                 'observe_native_review_assignment', 'collect_native_review'):
        if name in native:
            mocks.enter_context(patch.object(codex, name, return_value=native[name]))
        else:
            mocks.enter_context(patch.object(codex, name,
                side_effect=AssertionError('Unexpected native call in dispatcher fixture')))
    sys.argv = [{str(package / 'scripts/workflow.py')!r}] + sys.argv[1:]
    runpy.run_path(sys.argv[0], run_name='__main__')
"""
        env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        env.update(env_updates or {})
        result = subprocess.run([sys.executable, '-I', '-B', '-c', bootstrap,
            family, *parts, '--cwd', str(cwd or self.nested), *flags], cwd=self.base,
            input=raw if raw is not None else json.dumps(request).encode() if request is not None else b'',
            env=env, capture_output=True, timeout=12)
        self.assertEqual(result.stderr, b'')
        self.assertNotIn(b'PRIVATE_MARKER', result.stdout)
        self.assertNotIn(b'Traceback', result.stdout)
        return result.returncode, json.loads(result.stdout)

    def test_guidance_leaves_accept_surface_request_without_injected_client(self):
        # Catch status treated as flag-only, client injection, and wrong guidance dispatch.
        # One enrollment serves every case: status cases run first, while no guidance state exists;
        # guidance control is per surface; record publishes through the live claim that surface's
        # CLI claim case made (the API claims only if that case failed).
        scope = self.fixture.scope
        state_dir = scope.runtime.state_dir
        claimed = {}
        for action in ("status", "claim", "record"):
            for surface in ("codex-cli", "codex-desktop"):
                with self.subTest(surface=surface, action=action):
                    before={str(p):p.read_bytes() for p in state_dir.rglob('*') if p.is_file()}
                    request = self.cache | dict(client_surface=surface)
                    field, expected = "freshness", "unavailable"
                    if action == "claim":
                        request |= dict(trigger=dict(kind="routine", id="fixture"))
                        field, expected = "claimed", True
                    elif action == "record":
                        claim = claimed.get(surface) or delegation.claim_guidance(scope, request | dict(trigger=dict(kind="routine", id="fixture")))
                        guidance = dict(schema_version=1, client_surface=surface, checked_at="2026-01-01T00:00:00Z",
                            applicability=request["applicability"], sources=[dict(id="official",
                                url="https://example.org/official", sha256="b" * 64)],
                            recommendations=[dict(id="bounded", work="Bounded fixture", risk="Read-only fixture",
                                models=[], conditions=["Coordinator checks controls"], limitations=["Synthetic fixture"],
                                source_ids=["official"])])
                        request |= dict(token=claim["token"], generation=claim["generation"],
                            coordinator_ref="fixture:verified", guidance=guidance)
                        expected = "verified"
                    code, report = self.cli(["guidance", action], request)
                    self.assertTrue((state_dir / "runtime.sqlite3").is_file())
                    if action == "status":
                        self.assertEqual(before,{str(p):p.read_bytes() for p in state_dir.rglob("*") if p.is_file()})
                    self.assertEqual(code, 0, report)
                    self.assertEqual(report.get(field), expected, report)
                    if action == "claim":
                        claimed[surface] = report
                    if action == "record":
                        code, status = self.cli(["guidance", "status"], self.cache | dict(client_surface=surface))
                        self.assertEqual(code, 0, status)
                        self.assertEqual(status["snapshot_digest"], report["snapshot_digest"])

    def test_delegation_status_reads_cwd_scope_without_initializing_state(self):
        # Catch hook fallback, process-cwd routing, or status creating task state.
        self.canonical()
        code, report = self.cli(["status"], flags=("--client", "codex", "--session", "session-1"))
        self.assertFalse((self.base / "state").exists())
        self.assertEqual(code, 0, report)
        self.assertEqual(report.get("assignments"), [], report)
        self.assertIsNone(report["plan"])
        identity = dict(client="codex", session_id="session-1", task_id="cli-task", scope_ref="user:task")
        code, started = self.fixture.task_cli("start", identity | dict(mode="editable", decision_ref="user:accepted"))
        self.assertEqual(code, 0, started)
        code, status = self.cli(["status"], flags=("--client", "codex", "--session", "session-1"))
        self.assertEqual(code, 0, status)
        self.assertEqual(status["revision"], 1)
        code, unchanged = self.fixture.task_cli("status", None, "--client", "codex", "--session", "session-1")
        self.assertEqual(code, 0, unchanged)
        self.assertEqual(unchanged["task"], started["task"])

    def test_guidance_rejects_ambiguous_oversized_and_unknown_surface_requests(self):
        # Catch swallowed input, loose JSON parsing, or unknown-surface cache creation.
        self.canonical()
        cases = [
            ("non-object", "status", b"null", ()),
            ("duplicate-nested", "status", b'{"applicability":{"x":1,"x":2}}', ()),
            ("nonfinite", "claim", b'{"PRIVATE_MARKER":NaN}', ()),
            ("invalid-utf8", "record", b'{"PRIVATE_MARKER":"\xff"}', ()),
            ("external-cap", "status", json.dumps(self.cache).encode().ljust(128 * 1024 + 1), ()),
            ("guidance-cap", "status", json.dumps(self.cache | dict(profile_ids=["x" * (64 * 1024)])).encode(), ()),
            ("client-in-body", "status", json.dumps(self.cache | dict(client="codex")).encode(), ()),
            ("unknown-surface", "claim", json.dumps(self.cache | dict(client_surface="unknown-native",
                trigger=dict(kind="routine", id="fixture"))).encode(), ()),
            ("unknown-flag", "status", json.dumps(self.cache).encode(), ("--PRIVATE_MARKER",)),
            ("missing-record-fields", "record", json.dumps(self.cache).encode(), ()),
        ]
        before={str(p):p.read_bytes() for p in self.fixture.scope.runtime.state_dir.rglob('*') if p.is_file()}
        for label, action, raw, flags in cases:
            with self.subTest(case=label):
                code, report = self.cli(["guidance", action], raw=raw, flags=flags)
                self.assertFalse((self.base / "state").exists())
                self.assertEqual(code, 1, report)
                self.assertFalse(report["ok"])
                self.assertEqual(before,{str(p):p.read_bytes() for p in self.fixture.scope.runtime.state_dir.rglob("*") if p.is_file()})
                if label == "guidance-cap":
                    self.assertIn("bounded", report["error"].lower())


    def test_coordinator_reservation_and_reconciliation_are_model_free_cli_pairs(self):
        self.canonical()
        identity = dict(client='codex', session_id='session-1', task_id='cli-coordinator', scope_ref='user:task')
        self.assertEqual(self.fixture.task_cli('start', identity | dict(mode='editable', decision_ref='user:accepted'))[0], 0)
        request = identity | dict(expected_revision=1, dispatch_id='coordinator-cli',
            claims=[dict(kind='service', key='fixture:cli', mode='write', host='local')],
            operation=dict(id='coordinator-cli', kind='coordinator-work'), reason='One explicit disposable operation',
            ownership_ref='fixture:accepted-owner', stop_conditions=['Stop after operation'])
        code, admitted = self.cli(['coordinator', 'reserve'], request)
        self.assertEqual(code, 0, admitted)
        self.assertTrue(admitted['reservation_admitted'])
        self.assertFalse(admitted['replayed'])
        code, replay = self.cli(['coordinator', 'reserve'], request)
        self.assertEqual(code, 0, replay)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['attempt_id'], admitted['attempt_id'])
        scope = self.fixture.scope
        from runtime import evidence
        with state._database(scope) as db:
            record = next(r for r in delegation._ledger(scope, db) if r['id'] == admitted['attempt_id'])
            task = state._task(db, state._scope_id(scope), 'codex', 'session-1')
        bindings = dict(operation_id='coordinator-cli', operation_digest=record['body']['operation']['digest'],
            claims_digest=evidence._digest(record['data']['claims']))
        observed = fixture_artifact(scope,identity, bindings | dict(observation='Fixture independent inspection; operation and descendants stopped'))
        proof = dict(version=2, kind='coordinator-operation-reconciliation', identity=identity, expected_pins=task['pins'],
            expected_revision=task['revision'], scope_id=state._scope_id(scope), attempt_id=record['id'], dispatch_id='coordinator-cli',
            assignment_digest=record['data']['assignment']['digest'], decision_ref=task['decision_ref'], operation_evidence=observed,
            resource_observations=observed, operation_stopped=True, operation_descendants_stopped=True,
            observation_ref='fixture:independent-host-observation', coordinator_ref='fixture:inspected', **bindings)
        raw=json.dumps(proof).encode(); path=self.base/'coordinator-proof.json';path.write_bytes(raw);path.chmod(0o400)
        settlement = identity | dict(expected_revision=task['revision'], attempt_id=record['id'], host_proof=dict(path=str(path),
            sha256=hashlib.sha256(raw).hexdigest()), assessment=dict(outcome='completed', changed_paths=[],
            result_assessment='Fixture result independently checked', partial_change_assessment='Inspected claimed service effects',
            evidence=['fixture:checked'], coordinator_ref='fixture:inspected'))
        code, result = self.cli(['reconcile'], settlement)
        self.assertEqual(code, 0, result)
        self.assertEqual(result['outcome'], 'completed')
        code, replay = self.cli(['reconcile'], settlement)
        self.assertEqual(code, 0, replay)
        self.assertTrue(replay['replayed'])
        code, status = self.cli(['status'], flags=('--client', 'codex', '--session', 'session-1'))
        self.assertEqual(code, 0, status)
        self.assertIsNone(status['budgets'])
        self.assertEqual(len(status['assignments']), 1)
        self.assertIsNone(record['data']['candidate'])
        self.assertEqual(record['data']['resource_units'], 0)

    def test_scope_resolution_and_rejected_controls_never_initialize_unrelated_state(self):
        # setUp already enrolled a fresh unlinked canonical checkout.
        status = ['--client', 'codex', '--session', 'session-1']
        nested_repo = self.nested / 'inner'
        nested_repo.mkdir(); self.fixture.git(nested_repo, 'init', '-q')
        code, report = self.cli(['status'], flags=status, cwd=nested_repo)
        self.assertEqual(code, 1, report)
        self.assertFalse((self.base/'state').exists())
        outer=self.repo
        self.canonical()
        code, report = self.cli(['status'], flags=status, cwd=self.nested,
            env_updates=dict(GIT_DIR=str(outer/'.git'), GIT_WORK_TREE=str(outer)))
        self.assertEqual(code,0,report)
        self.assertIsNone(report['plan'])
        nested_repo=self.nested/'inner';nested_repo.mkdir();self.fixture.git(nested_repo,'init','-q')
        self.fixture.enroll(root=nested_repo)
        code, report = self.cli(['status'], flags=status, cwd=nested_repo)
        self.assertEqual(code, 1, report)
        self.assertFalse((self.base/'inner-state').exists())
        (nested_repo/startup.PROJECT_FILE).unlink()
        for leaf in (['plan'], ['prepare'], ['observe'], ['reconcile'], ['coordinator','reserve']):
            for request in ({'client':'unsupported','PRIVATE_MARKER':'private'}, {'client':'codex','PRIVATE_MARKER':'private'}):
                code, report = self.cli(leaf, request)
                self.assertEqual(code, 1, (leaf,report))
                self.assertFalse(report['ok'])
                self.assertFalse((self.base/'state').exists())
        for args in (['delegation'], ['delegation','plan'], ['delegation','guidance','status'],
                     ['delegation','coordinator','reserve'], ['delegation','status','--cwd',str(self.repo),'--client','unsupported','--session','x']):
            result = subprocess.run([sys.executable,'-I','-B',str(PACKAGE/'scripts/workflow.py'),*args],
                input=b'{}',capture_output=True,timeout=5)
            self.assertEqual(result.returncode,1)
            self.assertEqual(result.stderr,b'')
            self.assertFalse(json.loads(result.stdout)['ok'])

    def test_linked_checkout_cwd_wins_over_inherited_git_selectors(self):
        self.canonical(linked=True)
        scope=self.fixture.scope
        self.assertNotEqual(scope.repository_root,scope.primary_root)
        identity=dict(client='codex',session_id='linked-session',task_id='linked-task',scope_ref='user:linked')
        env=dict(GIT_DIR=str(scope.git_common_dir),GIT_WORK_TREE=str(scope.primary_root))
        code,started=self.cli(['start'],identity|dict(mode='editable',decision_ref='user:accepted'),family='task',cwd=self.nested,env_updates=env)
        self.assertEqual(code,0,started)
        code,status=self.cli(['status'],flags=('--client','codex','--session','linked-session'),cwd=self.nested,env_updates=env)
        self.assertEqual(code,0,status)
        self.assertEqual(status['revision'],1)
        self.assertEqual(started['task']['pins']['accepted_scope_digest'],scope.accepted_scope_digest)
        self.assertTrue((scope.runtime.state_dir/'runtime.sqlite3').is_file())


class DelegationLifecycleDispatcherTests(unittest.TestCase):
    cli = DelegationDispatcherTests.cli

    def setUp(self):
        from tests.test_delegation_review import Stage4NativeReviewTests
        self.fixture=Stage4NativeReviewTests(methodName='runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.base,self.repo=self.fixture.root,self.fixture.repo
        self.nested=self.repo/'nested';self.nested.mkdir()

    def test_worker_and_linked_review_dispatch_once_through_all_native_cli_leaves(self):
        f=self.fixture
        plan=f.plan_value()
        plan['jobs'].append(dict(id='review',role='code-reviewer',claims=[f.path_claim(mode='read')],dependencies=['edit'],max_attempts=3))
        code,result=self.cli(['plan'],f.request(plan=plan));self.assertEqual(code,0,result)
        worker_request=f.prepare_request()
        code,worker=self.cli(['prepare'],worker_request,native=dict(discover_native_workers=f.catalog))
        self.assertEqual(code,0,worker);self.assertTrue(worker['launch_admitted'])
        code,replay=self.cli(['prepare'],worker_request)
        self.assertEqual(code,0,replay);self.assertTrue(replay['replayed']);self.assertFalse(replay['launch_admitted'])
        self.assertEqual(replay['attempt_id'],worker['attempt_id'])
        from tests.test_delegation import AdmissionTests
        actual=AdmissionTests.lifecycle_observation(f,worker)
        observation=f.request(attempt_id=worker['attempt_id'],parent_turn_id='parent-turn',spawn_item_id='spawn-item')
        code,result=self.cli(['observe'],observation,native=dict(observe_native_worker=actual));self.assertEqual(code,0,result)
        code,repeat=self.cli(['observe'],observation,native=dict(observe_native_worker=actual))
        self.assertEqual(code,0,repeat);self.assertTrue(repeat['replayed'])
        proof=f.worker_reconciliation_request(worker)
        code,result=self.cli(['reconcile'],proof);self.assertEqual(code,0,result);self.assertEqual(result['outcome'],'completed')
        code,repeat=self.cli(['reconcile'],proof);self.assertEqual(code,0,repeat);self.assertTrue(repeat['replayed'])
        review_request=f.review_request()
        code,review=self.cli(['review','prepare'],review_request,family='evidence',native=dict(discover_native_workers=f.catalog))
        self.assertEqual(code,0,review);self.assertTrue(review['launch_admitted'])
        self.assertEqual(review['assignment_digest'],hashlib.sha256(review_request['assignment'].encode()).hexdigest())
        self.assertNotEqual(review['assignment_digest'],review['ledger_assignment_digest'])
        code,repeat=self.cli(['review','prepare'],review_request,family='evidence')
        self.assertEqual(code,0,repeat);self.assertEqual(repeat['attempt_id'],review['attempt_id']);self.assertFalse(repeat['launch_admitted'])
        actual=f.observed(review)
        code,result=self.cli(['observe'],f.request(attempt_id=review['attempt_id'],parent_turn_id='parent-turn',spawn_item_id='spawn-item'),
            native=dict(observe_native_review_assignment=actual));self.assertEqual(code,0,result)
        code,result=self.cli(['review','collect'],f.request(attempt_id=review['attempt_id'],parent_turn_id='parent-turn',spawn_item_id='spawn-item'),
            family='evidence',native=dict(collect_native_review=f.collection(review,actual)))
        self.assertEqual(code,0,result);self.assertEqual(result['attempt']['status'],'collected')
        code,result=self.cli(['review','assess'],f.request(attempt_id=review['attempt_id'],assessment=dict(
            coverage=['general','security'],criteria=['works'],qualification='Independent fixture expertise',
            independence='Separate observed child',semantic_coverage='Inspected exact fixture scope',dispositions=[],decision_refs=['user:accepted'])),family='evidence')
        self.assertEqual(code,0,result);self.assertEqual(result['attempt']['status'],'assessed')
        self.assertNotEqual(f.raw_assignment(review['attempt_id'])['status'],'settled')
        proof=f.worker_reconciliation_request(review,changed_paths=[])
        code,result=self.cli(['reconcile'],proof);self.assertEqual(code,0,result);self.assertEqual(result['outcome'],'completed')
        code,status=self.cli(['status'],flags=('--client','codex','--session','parent'))
        self.assertEqual(code,0,status);self.assertEqual(status['budgets']['attempts_consumed'],2)
        self.assertEqual(status['budgets']['resource_units_consumed'],2)
        self.assertEqual(status['capacity']['unresolved_managed_workers'],0)
        self.assertEqual({r['attempt_id'] for r in status['assignments']},{worker['attempt_id'],review['attempt_id']})
        with state._database(f.scope) as db:
            attempts=state._task(db,state._scope_id(f.scope),'codex','parent')['evidence']['attempts']
        self.assertEqual([a['id'] for a in attempts if a['kind']=='review'],[review['attempt_id']])
        self.assertEqual(self.cli(['prepare'],review_request)[0],1)

    def test_incompatible_pins_reject_mutation_without_state_or_artifact_changes(self):
        f=self.fixture
        with state._database(f.scope,write=True) as db:
            task=state._task(db,state._scope_id(f.scope),'codex','parent')
            task['pins']['runtime_digest']='0'*64
            # Seed a retained incompatible record without using the current mutation authority.
            db.execute('update tasks set data=? where scope_id=? and client=? and session_id=? and task_id=?',
                (json.dumps(task,separators=(',',':')),state._scope_id(f.scope),'codex','parent',task['task_id']))
        directory=f.scope.runtime.state_dir
        before={str(p.relative_to(directory)):p.read_bytes() for p in directory.rglob('*') if p.is_file()}
        request=f.identity|dict(expected_revision=task['revision'],dispatch_id='incompatible',claims=[f.path_claim()],
            operation=dict(id='incompatible',kind='coordinator-work'),reason='Fixture request',ownership_ref='fixture:owner',stop_conditions=['Stop'])
        code,result=self.cli(['coordinator','reserve'],request)
        self.assertEqual(code,1,result);self.assertFalse(result['ok'])
        self.assertEqual({str(p.relative_to(directory)):p.read_bytes() for p in directory.rglob('*') if p.is_file()},before)

if __name__ == "__main__":
    unittest.main()
