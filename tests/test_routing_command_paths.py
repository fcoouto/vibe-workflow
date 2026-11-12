"""F4 path requests and effort omission at the routing command (moved from tests/test_routing_command.py).

The shared `Base` and request helpers stay in tests/test_routing_command.py for their existing consumers. `Base` is
referenced through that module so this module never collects the imported test case a second time.
"""
import io
import json
from pathlib import Path
import unittest
from unittest import mock

from runtime import routing, subscription_billing
from scripts import routing as command
from tests import test_routing_command as command_tests
from tests.test_routing_command import SCOPE, check, facts, job, now, observations, prepare, table

CODEX = dict(strategy_id='s-codex', model='invented-model', effort='high', harness='codex-native', provider='openai',
             evidence_refs=['g1'])


class PathRequestTests(command_tests.Base):
    """F4.2-F4.4 at the routing command: shared selection and the internal preview, the operator's path
    qualification and the launching host's harnesses."""

    def setUp(self):
        super().setUp()
        from runtime import routing_observations as collector, routing_paths, routing_settings
        self.collector, self.paths = collector, routing_paths
        # No production billing producer exists: a labelled code-level double stands in for this module's flows.
        billing = mock.patch.object(collector.LocalSource, 'billing', lambda source, route, now:
                                     subscription_billing.double('command-paths')(route, now))
        billing.start()
        self.addCleanup(billing.stop)
        self.project = Path(self.temp.name).resolve() / 'checkout'
        self.project.mkdir()
        routing_settings.update(self.settings, dict(managed_projects=[str(self.project)]), 0)
        self.store_table(dict(table(), rows={'implementation|medium|medium': [CODEX]}))
        routing_paths.qualify(self.settings, 'codex-chatgpt-subscription', demand={'7d': 5}, evidence='e', now=now())
        collector.store_window(self.settings, 'chatgpt-subscription', dict(
            id='7d', limit=1000, used=100, length=604800, reset_at=now() + 3600, rolling=False, observed_at=now(),
            unit=collector.WINDOW_UNIT))
        self.service.close()
        self.service = command.Service(self.settings, self.ledger, project=self.project)

    def body(self, attempt='p1', harnesses=('codex-native',), **extra):
        value = dict(assignment=dict(attempt_id=attempt, task='task-' + attempt, scope=SCOPE), job=job())
        if harnesses is not None:
            value['constraints'] = dict(allowed_harnesses=list(harnesses))
        value.update(extra)
        return value

    def test_preview_and_prepare_share_selection(self):
        selected = []
        real = command.Service._select

        def spy(service, body):
            selected.append(real(service, body))
            return selected[-1]
        with mock.patch.object(command.Service, '_select', spy):
            preview = self.service.preview(self.body())
            admitted = self.call(dict(v=1, op='prepare', **self.body()))
        self.assertEqual(len(selected), 2)
        self.assertEqual(preview['state'], 'previewed', preview)
        self.assertEqual((preview['mode'], preview['shortlist']), ('ranked', ['s-codex']))
        self.assertEqual(preview['routes'], ['codex-chatgpt-subscription:s-codex'])
        self.assertEqual(preview['ranking']['strategy_ids'], admitted['ranking']['strategy_ids'])
        self.assertEqual(admitted['state'], 'admitted', admitted)
        self.assertEqual((admitted['admission']['route'], admitted['admission']['mode']),
                         ('codex-chatgpt-subscription:s-codex', 'ranked'))
        self.assertRegex(preview['routes_version'], r'^[0-9a-f]{16}$')

    def test_preview_never_opens_a_ledger(self):
        def refuse(*args, **kwargs):
            raise AssertionError('preview read a window, the ledger or a reader')
        before = sorted((path.name, path.read_bytes()) for path in self.settings.parent.iterdir() if path.is_file())
        with mock.patch.object(command.Service, '_ledger', refuse), \
                mock.patch.object(command.Service, '_workflow_binding', refuse), \
                mock.patch.object(self.collector.LocalSource, 'windows', refuse), \
                mock.patch.object(self.collector, 'refresh_needed', refuse), \
                mock.patch.object(self.collector, '_ledger_data', refuse), \
                mock.patch.object(routing, 'admit', refuse):
            preview = self.service.preview(self.body(workflow=dict(client='codex')))
        self.assertEqual(preview['state'], 'previewed', preview)
        self.assertFalse(self.ledger.exists())
        after = sorted((path.name, path.read_bytes()) for path in self.settings.parent.iterdir() if path.is_file())
        self.assertEqual(after, before)

    def test_preview_requires_collector_mode_and_harnesses(self):
        self.assertEqual(self.service.preview(self.body(observations=observations()))['reason'], 'invalid_request')
        for harnesses in (None, (), ('',)):
            with self.subTest(harnesses=harnesses):
                self.assertEqual(self.service.preview(self.body(harnesses=harnesses))['reason'], 'harness_required')
        unpinned = command.Service(self.settings, self.ledger)
        self.addCleanup(unpinned.close)
        self.assertEqual(unpinned.preview(self.body())['reason'], 'project_required')
        self.assertEqual(self.service.preview(self.body(job='broken'))['reason'], 'profile_unavailable')

    def test_preview_is_not_a_protocol_operation(self):
        self.assertNotIn('preview', command.FIELDS)
        self.assertEqual(self.call(dict(v=1, op='preview', **self.body()))['reason'], 'unknown_op')

    def test_managed_prepare_without_harnesses_is_refused(self):
        for revision, smart in ((1, True), (2, False)):
            self.assertEqual(self.call(dict(v=1, op='set', expected_revision=revision,
                                            change=dict(smart_routing=smart)))['state'], 'updated')
            for harnesses in (None, ()):
                with self.subTest(smart_routing=smart, harnesses=harnesses):
                    answer = self.call(dict(v=1, op='prepare', **self.body(harnesses=harnesses)))
                    self.assertEqual((answer['state'], answer['reason']), ('blocked', 'harness_required'))
        self.assertFalse(self.ledger.exists())  # refused before any ledger use

    def test_prepare_with_caller_observations_needs_no_harnesses(self):
        self.store_table(table())
        unmanaged = command.Service(self.settings, self.ledger)
        self.addCleanup(unmanaged.close)
        answer = unmanaged.handle(json.dumps(prepare('c1')).encode())
        self.assertEqual(answer['state'], 'admitted', answer)

    def test_qualify_path_is_one_shot_and_validates_its_request(self):
        request = dict(v=1, op='qualify_path', path='opencode-chatgpt-subscription', demand={'7d': 9}, evidence='r1')
        self.assertEqual(self.call(request)['reason'], 'operator_op_not_served')

        def main(body, *flags):
            stdout = io.BytesIO()
            code = command.main([body['op'], '--settings', str(self.settings), '--ledger', str(self.ledger), *flags],
                                io.BytesIO(json.dumps(body).encode()), stdout)
            return code, json.loads(stdout.getvalue())
        self.assertEqual(main(request)[1]['state'], 'qualified')
        self.assertEqual(self.paths.qualifications(self.settings)['opencode-chatgpt-subscription']['demand'], {'7d': 9})
        for change, reason in ((dict(demand={'5h': 9}), 'demand_invalid'), (dict(path='Nope'), 'invalid_request'),
                               (dict(path='unknown-path'), 'path_unknown'), (dict(evidence=''), 'evidence_invalid'),
                               (dict(path='test-only-subscription', demand={'5h': 1}), 'path_test_only')):
            with self.subTest(change):
                self.assertEqual(main(dict(request, **change))[1]['reason'], reason)
        self.assertEqual(main(dict(request, extra=1))[1]['reason'], 'invalid_request')
        removed = main(dict(v=1, op='unqualify_path', path='opencode-chatgpt-subscription'))[1]
        self.assertEqual((removed['state'], removed['removed']), ('unqualified', True))
        serving = command.Service(self.settings, self.ledger, serving=True)
        self.addCleanup(serving.close)
        self.assertEqual(serving.handle(json.dumps(dict(v=1, op='unqualify_path', path='x')).encode())['reason'],
                         'operator_op_not_served')


class OmittedEffortTests(command_tests.Base):
    """F4.2/F4.4 effort omission: an identity admitted without an effort carries the sentinel `none`; a host request
    that omits the effort (variant null, as OpenCode's `model.request` and a `provider/model` spawn report it) is the
    same identity. A nonempty other effort still mismatches, and no literal `none` is required from the host."""

    def test_an_omitted_variant_matches_an_identity_admitted_without_effort(self):
        self.store_table(dict(table(), rows={'implementation|medium|medium': [dict(
            strategy_id='s-sub', model='m', effort=None, harness='h', evidence_refs=['g1'])]}))
        admitted = self.call(prepare('o1', observations=observations(effort='none')))
        self.assertEqual(admitted['state'], 'admitted', admitted)
        sent = self.call(dict(v=1, op='dispatch', attempt_id='o1', request=facts(variant='none')))
        self.assertEqual(sent['state'], 'dispatched', sent)
        token, session = sent['authority'], sent['session_id']
        for variant, expected in ((None, 'allow'), ('none', 'allow'), ('high', 'route_mismatch')):
            with self.subTest(variant=variant):
                answer = self.call(check('o1', session_id=session, authority=token, request=facts(variant=variant)))
                self.assertEqual(answer.get('reason', answer['state']), expected, answer)
        self.assertEqual(self.call(check('o1', session_id=session, authority=token,
                                         request=facts(variant='')))['reason'], 'invalid_request')

    def test_an_omitted_variant_never_matches_an_identity_admitted_with_an_effort(self):
        admitted = self.call(prepare('o2'))  # admitted with effort `high`
        self.assertEqual(admitted['state'], 'admitted', admitted)
        sent = self.call(dict(v=1, op='dispatch', attempt_id='o2', request=facts()))
        answer = self.call(check('o2', session_id=sent['session_id'], authority=sent['authority'],
                                 request=facts(variant=None)))
        self.assertEqual(answer['reason'], 'route_mismatch')


if __name__ == '__main__':
    unittest.main()
