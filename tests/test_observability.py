"""Pure preparation of existing disposable task-status reports; no live telemetry."""

from copy import deepcopy
import importlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

from runtime import state
from tests.fixture_canonical_birth import fresh_canonical


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        fixture, acquired, observation = fresh_canonical(self)
        self.root, self.scope = fixture.base, acquired.resolution.scope

    def prepare(self, report, **kwargs):
        self.assertIsNotNone(importlib.util.find_spec('runtime.observability'),
                             'task-status preparation helper is missing')
        return importlib.import_module('runtime.observability').prepare_task_status(report, **kwargs)

    def report(self, *, canonical=True, start=True, client='codex'):
        if start:
            state.start_task(self.scope, dict(client='codex', session_id='session', task_id='task',
                scope_ref='PRIVATE scope text', mode='read-only', decision_ref='PRIVATE authorization'))
        report = state.task_status(self.scope, 'codex', 'session')
        if not canonical and report['task'] is not None:
            # A supplied retained shape, not current legacy runtime authority.
            report['task']['pins'] = dict(schema_version=2, policy_digest='policy', runtime_digest='runtime')
            report['task'].pop('accepted_identity')
        if client != 'codex' and report['task'] is not None:
            # Pure supplied data does not establish a qualified Claude client.
            report['task']['client'] = client
            report['task']['accepted_identity']['client'] = client
        return report

    def test_disabled_does_not_inspect_report(self):
        class Unreadable:
            def __getattribute__(self, name):
                raise AssertionError('Disabled preparation inspected its input')
        self.assertIsNone(self.prepare(Unreadable()))
        self.assertIsNone(self.prepare(Unreadable(), enabled=False))
        with self.assertRaises(ValueError):
            self.prepare(Unreadable(), enabled=1)

    def test_real_canonical_status_projects_identifiers_without_claiming_authority(self):
        report = self.report()
        original = deepcopy(report)
        result = self.prepare(report, enabled=True)
        self.assertTrue(result['preparation_only'])
        self.assertEqual(result['source'], 'runtime.state.task_status')
        self.assertEqual(result['task'], dict(client='codex', session_id='session', task_id='task',
            state='TRIAGE', revision=1, pins=report['task']['pins'],
            accepted_identity={key: report['task']['accepted_identity'][key]
                               for key in ('project_id', 'repository_id', 'checkout_id')}))
        self.assertEqual(result['generation'], 0)
        self.assertIsNone(result['historical'])
        self.assertEqual(result['gaps'], ['source_authenticity_unverified', 'source_freshness_unverified',
                                         'export_eligibility_unverified', 'current_readiness_unverified'])
        for field in ('usage', 'charges', 'native_model', 'native_settings', 'worker_coverage'):
            self.assertIsNone(result[field])
        self.assertEqual(report, original)
        result['task']['pins']['runtime_digest'] = 'changed'
        result['task']['accepted_identity']['project_id'] = 'changed'
        self.assertEqual(report, original)

    def test_supplied_legacy_shape_does_not_invent_canonical_scope(self):
        result = self.prepare(self.report(canonical=False), enabled=True)
        self.assertIsNone(result['task']['accepted_identity'])
        self.assertIn('canonical_identity_unavailable', result['gaps'])
        self.assertIsNone(result['usage'])

    def test_real_empty_status_preserves_absence(self):
        result = self.prepare(self.report(start=False), enabled=True)
        self.assertIsNone(result['task'])
        self.assertIsNone(result['historical'])
        self.assertEqual(result['generation'], 0)
        self.assertIn('task_unavailable', result['gaps'])

    def test_supplied_claude_status_does_not_inherit_codex_qualification(self):
        result = self.prepare(self.report(client='claude-code'), enabled=True)
        self.assertEqual(result['task']['client'], 'claude-code')
        self.assertIn('source_authenticity_unverified', result['gaps'])
        self.assertIsNone(result['native_settings'])

    def test_excluded_payloads_are_not_traversed_or_copied(self):
        report = self.report()
        secret = 'PRIVATE prompt completion review path credential'
        report['limitations'] = [secret]
        report['blockers'] = [dict(reason=secret, reference=secret)]
        report['task'].update(scope_ref=secret, reason=secret, reference=secret,
                              evidence={'output': secret, 'usage': {'tokens': 10, 'charges': 2}},
                              conditions=[dict(reason=secret)])
        report['task']['accepted_identity']['project_root'] = secret
        result = self.prepare(report, enabled=True)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('blockers', result)
        self.assertNotIn('limitations', result)
        self.assertNotIn('evidence', result['task'])
        self.assertIsNone(result['usage'])
        self.assertIsNone(result['charges'])
        # Ignored fields must not be traversed just to construct the projection.
        report['task']['evidence'] = object()
        report['limitations'] = [object()]
        self.assertEqual(self.prepare(report, enabled=True), result)

    def test_historical_status_never_becomes_current_or_merge_authority(self):
        report = self.report()
        # This marker matches _closed_status's shape; it is not a closure qualification.
        report['task']['state'] = 'CANCELLED'
        report['historical'] = dict(historical=True, outcome='CANCELLED', current=False,
            implementation_verified=False, delivery_complete=False, merge_authority=False,
            status='retained', compact=dict(name='a' * 32 + '.json', digest='b' * 64))
        before = deepcopy(report)
        result = self.prepare(report, enabled=True)
        self.assertEqual(result['historical'], report['historical'])
        self.assertFalse(result['historical']['current'])
        self.assertFalse(result['historical']['merge_authority'])
        result['historical']['compact']['digest'] = 'changed'
        self.assertEqual(report, before)
        report['historical'].update(status='unavailable', compact=None)
        result = self.prepare(report, enabled=True)
        self.assertIn('historical_support_unavailable', result['gaps'])
        self.assertIsNone(result['usage'])

    def test_retired_status_is_accepted_only_in_a_historical_report(self):
        for retired in ('READY', 'BLOCKED'):
            with self.subTest(state=retired):
                report = self.report(start=retired == 'READY')
                report['task']['state'] = retired
                with self.assertRaises(ValueError):
                    self.prepare(deepcopy(report), enabled=True)
                report['historical'] = dict(historical=True, outcome=retired, current=False,
                    implementation_verified=False, delivery_complete=False, merge_authority=False,
                    status='retained', compact=dict(name='a' * 32 + '.json', digest='b' * 64))
                result = self.prepare(report, enabled=True)
                self.assertEqual(result['task']['state'], retired)
                self.assertFalse(result['historical']['current'])

    def test_historical_authority_flags_and_compact_references_are_validated(self):
        report = self.report()
        historical = dict(historical=True, outcome='TRIAGE', current=False,
            implementation_verified=False, delivery_complete=False, merge_authority=False,
            status='retained', compact=dict(name='a' * 32 + '.json', digest='b' * 64))
        for key, value in (('current', True), ('implementation_verified', True),
                           ('delivery_complete', True), ('merge_authority', True),
                           ('historical', False), ('outcome', 'DONE'), ('status', 'PRIVATE'),
                           ('compact', None), ('compact', {'name': '/PRIVATE', 'digest': 'b' * 64})):
            with self.subTest(field=key), self.assertRaises(ValueError):
                self.prepare(report | {'historical': historical | {key: value}}, enabled=True)

    def test_supplied_ready_state_and_counters_do_not_become_current_readiness(self):
        report = self.report()
        report['generation'] = 5
        report['task'].update(state='DONE', revision=7)
        result = self.prepare(report, enabled=True)
        self.assertEqual(result['generation'], 5)
        self.assertEqual(result['task']['state'], 'DONE')
        self.assertEqual(result['task']['revision'], 7)
        self.assertIn('current_readiness_unverified', result['gaps'])
        self.assertNotIn('merge_authority', result)

    def test_malformed_source_shapes_fail_without_echoing_values(self):
        valid = self.report()
        bad = []
        for key in ('ok', 'task', 'generation', 'blockers', 'limitations'):
            item = deepcopy(valid)
            del item[key]
            bad.append(item)
        for value in (False, 1, 'PRIVATE'):
            bad.append(valid | {'ok': value})
        for value in (-1, True, 1.5, 1 << 63):
            bad.append(valid | {'generation': value})
        for key, value in (('client', 'other'), ('session_id', ''), ('task_id', 'x' * 129),
                           ('state', 'PRIVATE'), ('state', []), ('revision', 0), ('revision', True),
                           ('session_id', 'PRIVATE\ncredential')):
            item = deepcopy(valid)
            item['task'][key] = value
            bad.append(item)
        item = deepcopy(valid)
        item['task']['accepted_identity']['client'] = 'claude-code'
        bad.append(item)
        item = deepcopy(valid)
        item['task']['accepted_identity']['checkout_id'] = item['task']['accepted_identity']['repository_id']
        bad.append(item)
        item = deepcopy(valid)
        item['task']['pins']['runtime_digest'] = 'PRIVATE'
        bad.append(item)
        item = deepcopy(valid)
        item['task']['pins']['schema_version'] = True
        bad.append(item)
        bad += [None, [], {'ok': True}, valid | {'historical': {'current': True}},
                valid | {'blockers': 'PRIVATE'}, valid | {'limitations': 'PRIVATE'}]
        for report in bad:
            with self.subTest(report_type=type(report).__name__):
                with self.assertRaises(ValueError) as caught:
                    self.prepare(report, enabled=True)
                self.assertNotIn('PRIVATE', str(caught.exception))
                self.assertLess(len(str(caught.exception)), 150)

    def test_scalar_and_container_limits_reject_unbounded_projection(self):
        report = self.report()
        for key, value in (('session_id', 'x' * 1025), ('revision', 1 << 63), ('task_id', 'x' * 129)):
            item = deepcopy(report)
            item['task'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.prepare(item, enabled=True)
        for key in ('blockers', 'limitations'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.prepare(report | {key: [None] * 65}, enabled=True)
        item = deepcopy(report)
        item['task']['accepted_identity']['project_id'] = 'x' * 129
        with self.assertRaises(ValueError):
            self.prepare(item, enabled=True)

    def test_custom_scalars_are_rejected_without_executing_their_methods(self):
        class Unexpected:
            def __eq__(self, other):
                raise AssertionError('Preparation invoked a supplied scalar')
        report = self.report()
        report['task']['accepted_identity']['client'] = Unexpected()
        with self.assertRaises(ValueError):
            self.prepare(report, enabled=True)
        report['task']['accepted_identity']['client'] = 'codex'
        report['task']['state'] = 'CANCELLED'
        report['historical'] = dict(historical=True, outcome=Unexpected(), current=False,
            implementation_verified=False, delivery_complete=False, merge_authority=False,
            status='unavailable', compact=None)
        with self.assertRaises(ValueError):
            self.prepare(report, enabled=True)

    def test_preparation_import_and_call_have_no_external_or_core_side_effects(self):
        report = self.report()
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        source = '''
import json, sys
from runtime.observability import prepare_task_status
assert 'runtime.state' not in sys.modules
assert 'runtime.evidence' not in sys.modules
report = json.loads(sys.stdin.read())
def audit(event, args):
    if event in {'open', 'os.listdir', 'os.scandir', 'os.mkdir', 'os.remove', 'os.rename',
                 'os.putenv', 'socket.__new__', 'socket.connect', 'subprocess.Popen', 'os.system'}:
        raise AssertionError('Preparation attempted an external action')
sys.addaudithook(audit)
print(json.dumps(prepare_task_status(report, enabled=True)))
'''
        result = subprocess.run([sys.executable, '-B', '-c', source], input=json.dumps(report),
            text=True, capture_output=True, cwd=Path(__file__).resolve().parents[1], timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['task']['task_id'], 'task')
        self.assertEqual({p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)


if __name__ == '__main__':
    unittest.main()
