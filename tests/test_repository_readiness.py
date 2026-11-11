"""Gate lifecycle fixtures are not qualification of a production publishing source."""
from contextlib import contextmanager
from copy import deepcopy
import sys
import time
import unittest
from unittest.mock import patch

from runtime import delegation, evidence, repository_gate as gate, state
from runtime.adapters.github_delivery import GitHubDelivery
from tests import test_delivery


class RepositoryReadinessTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_delivery.MergeVerificationTests('runTest')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.local = self.fixture.fixture
        self.scope = self.local.scope
        self.request = self.local.request(action='ready', operation_id='metadata', publication_id='main')
        plan = evidence._load_plan(self.scope, self.local.identity)
        self.candidate = evidence.capture_candidate(self.scope, plan)
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
        self.snapshot = evidence._artifact(self.scope, self.candidate, owner=evidence._artifact_owner(self.scope, task))
        self.binding = dict(id='main', host='github.com', repository='fixture/project', target='main',
            branch='work', pull_request=7, repository_id=21, head=self.candidate['head'], snapshot=self.snapshot)

    def acquire(self, request=None):
        acquire = getattr(gate, 'acquire_readiness', None)
        self.assertTrue(callable(acquire), 'Delivery requires the concrete Gate context')
        return acquire(self.scope, request or self.request, self.binding)

    def source(self, *, withdrawal=False):
        test = self
        class FixtureSource(gate._QualifiedReadinessSource):
            remote_effects = 'none'
            expires_at = time.monotonic() + 120
            def __init__(self):
                self.repository = GitHubDelivery(executable=sys.executable, host='github.com',
                    run=self.forbidden, mutate=self.forbidden)
                self.calls = 0
                request = test.local.readiness_owner(session='parent', identity=test.local.identity,
                    claims=[dict(kind='service', key='fixture-pr-metadata', host='local', mode='write')])
                reserved = delegation._reserve_operation(test.scope, request, role='orchestrator')
                self.operation = reserved['attempt_id']
                test.request = test.local.request(action='ready', operation_id='metadata', publication_id='main')
                with state._database(test.scope) as db:
                    task = state._task(db, state._scope_id(test.scope), 'codex', 'parent')
                    operation = next(row for row in delegation._ledger(test.scope, db) if row['id'] == self.operation)
                owner = evidence._artifact_owner(test.scope, task)
                self.dependencies = {key:evidence._artifact(test.scope,
                    dict(kind='fixture-' + key, version=1), owner=owner)
                    for key in ('native', 'gate', 'server', 'definitions', 'custody', 'operation')}
                self.dependencies['operation'] = operation['data']['assignment']
            def forbidden(self, *args, **kwargs):
                raise AssertionError('No native/provider execution in source fixtures')
            def recheck(self, scope, request, binding, *, withdrawal_only):
                test.assertIs(scope, test.scope)
                test.assertEqual(request, test.request if not withdrawal else test.request | dict(action='reopen'))
                test.assertEqual(binding, test.binding)
                test.assertEqual(withdrawal_only, withdrawal)
                # A remote-call boundary must allow an independent local writer.
                with self.writer() as db:
                    test.assertTrue(db.in_transaction)
                self.calls += 1
                facts = dict(version=1, kind='repository-readiness-source', binding=deepcopy(binding),
                    base=test.candidate['base'], tree=test.local.git('rev-parse', 'HEAD^{tree}'),
                    definitions_digest='a' * 64, source_binding_digest='b' * 64,
                    check_run=None if withdrawal else dict(id=31, repository_id=binding['repository_id'], app_id=41,
                        head_sha=binding['head'], name='Readiness', external_id='fixture-operation',
                        status='completed', conclusion='success', merge_authority=False),
                    dependencies=self.dependencies, outcome='blocked' if withdrawal else 'ready',
                    limitations=['Fixture source only; production qualification absent'])
                with state._database(scope) as db:
                    task = state._task(db, state._scope_id(scope), 'codex', 'parent')
                return evidence._artifact(scope, facts, owner=evidence._artifact_owner(scope, task))
            @contextmanager
            def writer(self):
                with delegation._reservation_write(test.scope) as db:
                    yield db
            def settle(self, db, outcome):
                operation = next(row for row in delegation._ledger(test.scope, db) if row['id'] == self.operation)
                test.assertEqual(operation['data']['status'], 'prepared')
                task = state._task(db, state._scope_id(test.scope), 'codex', 'parent')
                ref = evidence._artifact(test.scope, dict(version=1, kind='fixture-metadata-reconciliation',
                    assignment=operation['data']['assignment']), owner=evidence._artifact_owner(test.scope, task), db=db)
                operation['data'].update(status='settled', reconciliation=ref, result=dict(outcome=outcome))
                db.execute('update assignments set data=? where id=?',
                    (evidence._raw(operation['data']).decode(), self.operation))
            def finalize(self, publish, *, db=None):
                if db is None:
                    with self.writer() as writer:
                        return self.finalize(publish, db=writer)
                result = publish(db)
                self.settle(db, 'completed')
                return result
            def cancel(self):
                with self.writer() as db:
                    self.settle(db, 'cancelled')
        return FixtureSource()

    def test_actual_context_records_dependencies_and_finalizes_existing_writer(self):
        self.assertTrue(callable(getattr(gate, 'acquire_readiness', None)), 'Delivery requires the concrete Gate context')
        source = self.source()
        with patch.object(gate, '_acquire_qualified_readiness_source', return_value=source):
            with self.acquire() as held:
                self.assertIs(held.repository, source.repository)
                self.assertFalse(held.withdrawal_only)
                self.assertEqual(evidence._read(self.scope, held.reference)['kind'], 'repository-merge-readiness')
                verification = held.local_verification
                def publish(db):
                    self.assertTrue(db.in_transaction)
                    reference = held.reference
                    body = evidence._read(self.scope, reference, db=db)
                    self.assertEqual(body['local_verification'], verification)
                    return reference
                reference = held.finalize(publish)
                self.assertEqual(evidence._read(self.scope, reference)['outcome'], 'ready')
            with self.assertRaises(gate.GateError):
                held.repository
        self.assertGreaterEqual(source.calls, 2)

    def test_uncertain_effect_and_expired_context_retain_reservation(self):
        self.assertTrue(callable(getattr(gate, 'acquire_readiness', None)), 'Delivery requires the concrete Gate context')
        source = self.source()
        with patch.object(gate, '_acquire_qualified_readiness_source', return_value=source):
            with self.acquire() as held:
                source.remote_effects = 'uncertain'
                source.expires_at = time.monotonic() - 1
                with self.assertRaises(gate.GateError):
                    held.finalize(lambda db:self.fail('Expired source reached local publication'))
        with state._database(self.scope) as db:
            records = delegation._ledger(self.scope, db)
        self.assertEqual(sum(row['data']['status'] == 'prepared' for row in records), 2)

    def test_withdrawal_never_requires_positive_verification_or_enters_ready_writer(self):
        self.assertTrue(callable(getattr(gate, 'acquire_readiness', None)), 'Delivery requires the concrete Gate context')
        source = self.source(withdrawal=True)
        (self.local.repo / 'app.py').write_text('value = 2\n')
        with patch.object(gate, '_acquire_qualified_readiness_source', return_value=source), \
                patch.object(evidence, 'acquire_merge_verification', side_effect=AssertionError('Withdrawal requested positive evidence')):
            with self.acquire(self.request | dict(action='reopen')) as held:
                self.assertTrue(held.withdrawal_only)
                with self.assertRaises(gate.GateError):
                    held.local_verification
                reference = held.finalize(lambda db: held.reference)
                self.assertEqual(evidence._read(self.scope, reference)['outcome'], 'blocked')

    def test_known_no_effect_close_cancels_owners_and_receipt_cannot_reissue(self):
        self.assertTrue(callable(getattr(gate, 'acquire_readiness', None)), 'Delivery requires the concrete Gate context')
        source = self.source()
        with patch.object(gate, '_acquire_qualified_readiness_source', return_value=source):
            with self.acquire() as held:
                reference = held.reference
        with state._database(self.scope) as db:
            records = delegation._ledger(self.scope, db)
        self.assertTrue(all(row['data']['status'] == 'settled' for row in records))
        self.assertEqual(sum(row['data']['result']['outcome'] == 'cancelled' for row in records), 2)
        with self.assertRaises(gate.GateError):
            with gate.acquire_readiness(self.scope, self.request | dict(receipt=reference), self.binding):
                self.fail('Serialized receipt issued a source context')

    def test_blocked_positive_verification_cancels_no_effect_metadata_owner(self):
        source = self.source()
        (self.local.repo / 'app.py').write_text('value = 2\n')
        with patch.object(gate, '_acquire_qualified_readiness_source', return_value=source):
            with self.assertRaises(state.StateError):
                with self.acquire():
                    self.fail('Stale evidence issued positive readiness')
        with state._database(self.scope) as db:
            operation = next(row for row in delegation._ledger(self.scope, db) if row['id'] == source.operation)
        self.assertEqual(operation['data']['status'], 'settled')
        self.assertEqual(operation['data']['result']['outcome'], 'cancelled')


class UnqualifiedSourceTests(unittest.TestCase):
    def test_json_and_missing_host_source_cannot_issue_context(self):
        self.assertTrue(callable(getattr(gate, 'acquire_readiness', None)), 'Delivery requires the concrete Gate context')
        with self.assertRaises(gate.GateError):
            with gate.acquire_readiness({}, {}, {}):
                self.fail('Caller JSON issued readiness authority')


if __name__ == '__main__':
    unittest.main()
