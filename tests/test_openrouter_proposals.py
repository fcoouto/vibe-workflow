"""Behavioral boundaries: replay, cache lifecycle, attribution and private storage."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from runtime import openrouter_settings as settings
from tests.openrouter_fixtures import enroll, configure, fixture_command

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/openrouter_proposals.py'
PROFILE = dict(family='coding', language='Python', framework=None, difficulty='small', scope='one file', verification='unit tests')
METADATA = dict(models=[dict(id='model/v1', context_length=10000, max_output_tokens=1000)], endpoints=[dict(model='model/v1', provider='p', context_length=9000, max_output_tokens=1000, modalities=['text'], supported_parameters=['reasoning'], pricing={'output': '0.01'}, health=None)], benchmarks=[dict(model='model/v1', coding=None, native_reference='native')], task_usage=[], coverage={'complete': True, 'source': 'fixture'})


class ProposalCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SCRIPT.exists():
            spec = importlib.util.spec_from_file_location('proposals', SCRIPT)
            cls.api = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.api)
        else:
            cls.api = None

    def setUp(self):
        self.assertIsNotNone(self.api, 'standalone proposal helper must exist')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        self.scope = enroll(self)
        self.repo = self.scope.repository_root
        self.settings = configure(self.repo, routes=('proposals',))
        self.store = Path(self.settings['proposal_store'])

    def call(self, action, request=None, now=100):
        return self.api.run(action, self.store, request or {}, now=now, cwd=self.repo)

    def scoped_request(self, *, saved=True, automatic=True):
        from runtime import external_delegation as external
        from tests.test_external_delegation import permission
        consent = permission('openrouter', model='model/v1', provider='p', effort=None)
        consent['automatic'] = automatic
        disabled = settings.disable(self.repo, dict(expected_revision=self.settings['revision']))
        configured = settings.configure(self.repo, dict(expected_revision=disabled['revision'], routes=['proposals'],
            opencode=None, auth_data=None, external_permission=consent if saved else None))
        self.settings = settings.enable(self.repo, dict(expected_revision=configured['revision']))
        if not saved:
            consent = external.effective_permission(None, self.settings['activation'], service='openrouter',
                mode='proposal', model='model/v1', provider='p', effort=None, class_name='investigation')
        (self.repo / 'src').mkdir()
        (self.repo / 'src/code.py').write_text('source bytes\n')
        packet = external.prepare(self.repo, consent, [dict(kind='source', path='src/code.py')])
        self.publish()
        return dict(self.begin_request(), external=dict(packet['binding'], automatic=True, local_preferred=False,
            class_name='investigation', coordinator='native-coordinator', reviewer='native-reviewer',
            integration_available=True))

    def publish(self):
        token = self.call('claim')['refresh_token']
        return self.call('publish', dict(refresh_token=token, metadata=METADATA))

    def bind_request(self, request):
        from runtime import external_delegation as external
        consent = external.effective_permission(self.settings['external_permission'], self.settings['activation'],
            service='openrouter', mode='proposal', model=request['model'], provider=request['provider'],
            effort=request['effort'], class_name='investigation')
        packet = external.prepare(self.repo, consent, [dict(kind='text', text='Source-free fixture objective', sources=[])])
        return dict(request, external=dict(packet['binding'], automatic=True, local_preferred=False,
            class_name='investigation', coordinator='native-coordinator', reviewer='native-reviewer',
            integration_available=True))

    def begin_request(self, task='task-1'):
        return self.bind_request(dict(task_id=task, profile=PROFILE, model='model/v1', provider='p', effort=None,
                    snapshot_digest=self.call('status')['snapshot_digest'], approval_ref='approval/1', base_ref='git:abc',
                    output_tokens=500, timeout_seconds=60,
                    visibility=dict(version=2, role='explorer-agent', specialty='Proposal analysis'),
                    requirements=dict(context_tokens=100, output_tokens=500, modalities=['text'], parameters=[])))

    def cli(self, action, request):
        return subprocess.run(fixture_command(self, SCRIPT, action, '--store', self.store, '--cwd', self.repo), input=json.dumps(request), text=True, capture_output=True)


class ProposalTests(ProposalCase):
    def test_enabled_proposal_without_approval_ref_dispatches_once(self):
        request = self.scoped_request()
        del request['approval_ref']
        begin = self.call('begin', request)
        row = self.call('history')['records'][0]
        self.assertEqual(row['approval_ref'], self.settings['activation']['ref'])
        self.assertNotIn('approval_ref', request)
        dispatch = dict(request, attempt_token=begin['attempt_token'])
        with self.assertRaisesRegex(ValueError, 'external_attempt_changed'):
            self.call('dispatch', dict(dispatch, approval_ref=row['approval_ref']))
        self.assertIn('source bytes', self.call('dispatch', dispatch)['packet'])
        self.assertEqual(self.call('dispatch', dispatch)['fallback'], 'already_dispatched')

    def test_unbound_proposal_requires_external_binding_under_activation(self):
        self.publish()
        request = {key: value for key, value in self.begin_request().items()
                   if key not in ('external', 'approval_ref')}
        with self.assertRaisesRegex(ValueError, 'external_binding_required'):
            self.call('begin', request)
        self.assertEqual(self.call('history')['records'], [])

    def test_unbound_proposal_cannot_bypass_saved_automatic_false(self):
        request = {key: value for key, value in self.scoped_request(automatic=False).items()
                   if key not in ('external', 'approval_ref')}
        with self.assertRaisesRegex(ValueError, 'external_binding_required'):
            self.call('begin', request)
        self.assertEqual(self.call('history')['records'], [])

    def test_legacy_admitted_proposal_settles_without_new_dispatch(self):
        self.publish()
        request = {key: value for key, value in self.begin_request().items() if key != 'external'}
        state = json.loads(self.store.read_text())
        state['attempts'].append(dict(request, attempt_token='legacy-token', begun_at=100,
            management=dict(scope_id=self.settings['scope_id'], revision=self.settings['revision'])))
        self.store.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, 'external_binding_required'):
            self.call('dispatch', dict(request, attempt_token='legacy-token'))
        settings.disable(self.repo, dict(expected_revision=self.settings['revision']))
        recorded = self.call('record', dict(attempt_token='legacy-token', disposition='accepted',
            native_review_ref='legacy-review', native_checks=[dict(success=True, ref='legacy-check')]))
        self.assertTrue(recorded['recorded'])
        self.assertEqual(self.call('record', dict(attempt_token='legacy-token'))['fallback'], 'already_recorded')
        self.assertEqual(self.call('history')['records'][0]['outcome']['disposition'], 'accepted')

    def test_new_proposal_acceptance_requires_admitted_reviewer(self):
        request = self.scoped_request(saved=False)
        begin = self.call('begin', request)
        for disposition in ('accepted', 'repaired'):
            for reviewer in (None, 'native-coordinator'):
                with self.subTest(disposition=disposition, reviewer=reviewer), self.assertRaisesRegex(ValueError, 'independent_review'):
                    self.call('record', dict(attempt_token=begin['attempt_token'], disposition=disposition,
                        reviewer=reviewer, native_review_ref='review', native_checks=[dict(success=True, ref='tests')]))

    def test_scoped_proposal_without_saved_permission_uses_activation(self):
        request = self.scoped_request(saved=False)
        begin = self.call('begin', request)
        row = self.call('history')['records'][0]
        self.assertIsNone(self.settings['external_permission'])
        self.assertEqual(row['approval_ref'], request['approval_ref'])
        result = self.call('dispatch', dict(request, attempt_token=begin['attempt_token']))
        self.assertIn('source bytes', result['packet'])
        with self.assertRaisesRegex(ValueError, 'independent_review'):
            self.call('record', dict(attempt_token=begin['attempt_token'], disposition='accepted',
                reviewer='native-coordinator', native_review_ref='review', native_checks=[dict(success=True, ref='tests')]))

    def test_activation_packet_failure_never_falls_back_to_legacy_begin(self):
        request = self.scoped_request(saved=False)
        for binding, reason in ((dict(permission_digest='forged'), 'permission_changed'),
                                (dict(reviewer='native-coordinator'), 'independent_review')):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                self.call('begin', dict(request, external=dict(request['external'], **binding)))
        (self.repo / 'src/code.py').write_text('changed after preparation')
        with self.assertRaisesRegex(ValueError, 'source_changed'):
            self.call('begin', request)
        self.assertEqual(self.call('history')['records'], [])

    def test_activation_packet_does_not_survive_disable_and_reenable(self):
        request = self.scoped_request(saved=False)
        begin = self.call('begin', request)
        dispatch = dict(request, attempt_token=begin['attempt_token'])
        disabled = settings.disable(self.repo, dict(expected_revision=self.settings['revision']))
        self.assertEqual(self.call('dispatch', dispatch)['fallback'], 'openrouter_disabled')
        settings.enable(self.repo, dict(expected_revision=disabled['revision']))
        with self.assertRaisesRegex(ValueError, 'permission_changed'):
            self.call('dispatch', dispatch)

    def test_saved_permission_automatic_false_still_denies_automatic_proposal(self):
        request = self.scoped_request(automatic=False)
        with self.assertRaisesRegex(ValueError, 'automatic_selection_denied'):
            self.call('begin', request)
        manual = dict(request, external=dict(request['external'], automatic=False))
        begin = self.call('begin', manual)
        self.assertTrue(self.call('dispatch', dict(manual, attempt_token=begin['attempt_token']))['dispatched'])

    def test_scoped_proposals_use_admitted_checkout_when_cwd_is_a_subdirectory(self):
        from runtime import external_delegation as external
        request = self.scoped_request()
        subroot = self.repo / 'excluded'
        (subroot / 'src').mkdir(parents=True)
        (subroot / 'src/code.py').write_text('unapproved subdirectory bytes')
        consent = self.settings['external_permission']
        nested = external.prepare(subroot, consent, [dict(kind='source', path='src/code.py')])
        bad = dict(request, external=dict(request['external'], **nested['binding']))
        with self.assertRaisesRegex(ValueError, 'source_changed'):
            self.api.run('begin', self.store, bad, cwd=subroot, now=100)
        begin = self.api.run('begin', self.store, request, cwd=subroot, now=100)
        dispatch = dict(request, attempt_token=begin['attempt_token'])
        with self.assertRaisesRegex(ValueError, 'source_changed'):
            self.api.run('dispatch', self.store, dict(bad, attempt_token=begin['attempt_token']),
                         cwd=subroot, now=100)
        result = self.api.run('dispatch', self.store, dispatch, cwd=subroot, now=100)
        self.assertIn('source bytes', result['packet'])
        self.assertNotIn('unapproved subdirectory bytes', result['packet'])

    def test_scoped_proposal_dispatch_uses_checked_packet_once_and_requires_review(self):
        request = self.scoped_request()
        begin = self.call('begin', request)
        dispatch = dict(request, attempt_token=begin['attempt_token'])
        self.assertEqual(self.call('dispatch', dispatch, now=90000)['fallback'], 'stale_discovery')
        result = self.call('dispatch', dispatch)
        self.assertIn('source bytes', result['packet'])
        self.assertEqual(self.call('dispatch', dispatch)['fallback'], 'already_dispatched')
        self.assertNotIn('source bytes', self.store.read_text())
        with self.assertRaisesRegex(ValueError, 'independent_review'):
            self.call('record', dict(attempt_token=begin['attempt_token'], disposition='accepted',
                native_review_ref='review', native_checks=[dict(success=True, ref='tests')], reviewer='native-coordinator'))
        self.call('record', dict(attempt_token=begin['attempt_token'], disposition='unresolved'))
        self.assertEqual(self.call('begin', request)['fallback'], 'task_already_attempted')

    def test_scoped_proposal_denies_local_destination_source_and_revocation(self):
        request = self.scoped_request()
        for change, reason in ((dict(local_preferred=True), 'local_preference'),
                               (dict(reviewer='native-coordinator'), 'independent_review')):
            with self.assertRaisesRegex(ValueError, reason):
                self.call('begin', dict(request, external=dict(request['external'], **change)))
        with self.assertRaisesRegex(ValueError, 'destination'):
            self.call('begin', dict(request, provider='unapproved'))
        begin = self.call('begin', request)
        (self.repo / 'src/code.py').write_text('new source\n')
        with self.assertRaisesRegex(ValueError, 'source_changed'):
            self.call('dispatch', dict(request, attempt_token=begin['attempt_token']))
        settings.disable(self.repo, dict(expected_revision=self.settings['revision']))
        self.assertEqual(self.call('dispatch', dict(request, attempt_token=begin['attempt_token']))['fallback'],
                         'openrouter_disabled')

    def test_begin_requires_saved_checkout_enablement_and_selected_store(self):
        self.publish()
        request = self.begin_request()
        missing = self.api.run('begin', self.store, request, now=100)
        self.assertEqual(missing.get('fallback'), 'openrouter_scope_required')
        wrong = self.api.run('begin', self.store.parent / 'other.json', request, now=100, cwd=self.repo)
        self.assertEqual(wrong.get('fallback'), 'openrouter_configuration_mismatch')
        settings.disable(self.repo, {'expected_revision': self.settings['revision']})
        self.assertEqual(self.call('begin', request).get('fallback'), 'openrouter_disabled')
        self.assertEqual(json.loads(self.store.read_text())['attempts'], [])

    def test_admitted_proposal_can_settle_after_disable(self):
        self.publish()
        result = self.call('begin', self.begin_request())
        self.assertEqual(self.call('history')['records'][0]['management'],
                         dict(scope_id=self.settings['scope_id'], revision=self.settings['revision']))
        settings.disable(self.repo, {'expected_revision': self.settings['revision']})
        recorded = self.call('record', dict(attempt_token=result['attempt_token'], disposition='rejected'))
        self.assertTrue(recorded['recorded'])
        self.assertEqual(self.call('begin', self.begin_request('another'))['fallback'], 'openrouter_disabled')

    def test_disable_cannot_pass_an_admission_held_by_another_process(self):
        program = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from runtime import openrouter_settings as settings
with settings.admission(Path(sys.argv[2]), 'proposals', store=Path(sys.argv[3])):
    print('admitted', flush=True)
    sys.stdin.readline()
'''
        child = subprocess.Popen(fixture_command(self, '-c', program, SCRIPT.parents[1], self.repo, self.store), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            import selectors
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(10), 'admission process must signal readiness')
            self.assertEqual(child.stdout.readline().strip(), 'admitted')
            command = fixture_command(self, SCRIPT.with_name('openrouter.py'), 'disable', '--cwd', self.repo)
            blocked = subprocess.run(command, input=json.dumps({'expected_revision': self.settings['revision']}),
                                     text=True, capture_output=True, timeout=10)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertFalse(json.loads(blocked.stdout)['ok'])
            _, error = child.communicate('\n', timeout=10)
            self.assertEqual(child.returncode, 0, error)
            self.assertFalse(settings.disable(self.repo, {'expected_revision': self.settings['revision']})['enabled'])
        finally:
            if child.poll() is None: child.kill(); child.wait()
            for stream in (child.stdin, child.stdout, child.stderr): stream.close()

    def test_begin_keeps_valid_roles_and_ignores_missing_or_invalid_metadata(self):
        self.publish()
        request = self.begin_request()
        missing = {key: value for key, value in request.items() if key != 'visibility'}
        for index, item in enumerate((missing, dict(request, visibility=dict(request['visibility'], role='Default')))):
            result = self.call('begin', dict(item, task_id=f'optional-{index}'))
            self.call('record', dict(attempt_token=result['attempt_token'], disposition='unresolved'))
        self.assertIn('attempt_token', self.call('begin', request))
        records = self.call('history')['records']
        self.assertTrue(all('visibility' not in row for row in records[:2]))
        self.assertEqual(records[2]['visibility'], request['visibility'])

    def test_cache_reuse_expiry_and_failed_refresh_preserve_original_snapshot(self):
        self.publish()
        before = self.call('status')
        self.assertTrue(before['fresh'])
        self.assertNotIn('refresh_token', self.call('claim'))
        stale = self.call('status', now=86501)
        self.assertFalse(stale['fresh'])
        token = self.call('claim', now=86501)['refresh_token']
        failed = self.call('publish', dict(refresh_token=token, failure='offline'), now=86502)
        self.assertEqual(failed['snapshot_digest'], before['snapshot_digest'])
        self.assertEqual(failed['fetched_at'], 100)
        self.assertFalse(failed['refresh_eligible'])
        self.assertNotIn('refresh_token', self.call('claim', now=86503))
        self.assertIn('refresh_token', self.call('claim', now=90103))

    def test_claim_lease_supersedes_old_publisher(self):
        first = self.call('claim')['refresh_token']
        self.assertNotIn('refresh_token', self.call('claim'))
        second = self.call('claim', now=221)['refresh_token']
        with self.assertRaises(ValueError):
            self.call('publish', dict(refresh_token=first, metadata=METADATA), now=222)
        self.call('publish', dict(refresh_token=second, metadata=METADATA), now=222)

    def test_triage_preserves_missing_scores_and_reports_constraints(self):
        self.publish()
        request = dict(profile=PROFILE, requirements=dict(context_tokens=100, output_tokens=500, modalities=['text'], parameters=['reasoning']))
        result = self.call('triage', request)
        self.assertEqual(len(result['candidates']), 1)
        self.assertIsNone(result['candidates'][0]['benchmarks'][0]['coding'])
        request['requirements'].update(context_tokens=9500, parameters=['tools'])
        result = self.call('triage', request)
        self.assertEqual(result['candidates'], [])
        self.assertIn('context_tokens', result['incompatible'][0]['reasons'])
        self.assertIn('parameter:tools', result['incompatible'][0]['reasons'])
        self.assertEqual(self.call('triage', request, now=86501)['fallback'], 'stale_discovery')

    def test_begin_replay_and_one_pending_call(self):
        self.publish()
        request = self.begin_request()
        token = self.call('begin', request)['attempt_token']
        self.assertNotIn('attempt_token', self.call('begin', request))
        self.assertEqual(self.call('begin', self.begin_request('task-2'))['fallback'], 'pending_attempt')
        self.call('record', dict(attempt_token=token, disposition='unresolved', observations={'local_timeout': True, 'cost': None}))
        self.assertNotIn('attempt_token', self.call('begin', request))
        self.assertIn('attempt_token', self.call('begin', self.begin_request('task-2')))
        history = self.call('history')['records']
        self.assertIsNone(history[0]['outcome']['observations']['cost'])
        self.assertNotIn('credit', history[0])

    def test_begin_without_credit_preserves_server_owned_spending(self):
        self.publish()
        self.assertIn('attempt_token', self.call('begin', self.begin_request()))
        row = self.call('history')['records'][0]
        self.assertNotIn('credit', row)
        self.assertNotIn('credit_checked_at', row)

    def test_legacy_credit_observations_neither_gate_nor_persist(self):
        self.publish()
        for index, credit in enumerate((None, {'observed_at': 0, 'remaining': 0},
                {'observed_at': 999, 'remaining': 1, 'key_remaining': 0},
                {'observed_at': 100, 'remaining': 1, 'key_expires_at': 99})):
            with self.subTest(credit=credit):
                request = dict(self.begin_request(f'legacy-{index}'), credit=credit)
                result = self.call('begin', request)
                self.assertIn('attempt_token', result)
                self.call('record', dict(attempt_token=result['attempt_token'], disposition='transport_failure'))
        for row in self.call('history')['records']:
            self.assertNotIn('credit', row)
            self.assertNotIn('credit_checked_at', row)

    def test_attempt_execution_bounds_remain_enforced(self):
        self.publish()
        request = self.begin_request()
        request['timeout_seconds'] = 0
        with self.assertRaises(ValueError):
            self.call('begin', request)
        request = dict(self.begin_request(), output_tokens=8193)
        with self.assertRaises(ValueError):
            self.call('begin', request)

    def test_outcomes_require_native_reports_and_history_matches_exact_profile(self):
        self.publish()
        for index, disposition in enumerate(('accepted', 'repaired')):
            token = self.call('begin', self.begin_request(str(index)))['attempt_token']
            with self.assertRaises(ValueError):
                self.call('record', dict(attempt_token=token, disposition=disposition))
            self.call('record', dict(attempt_token=token, disposition=disposition, reviewer='native-reviewer', native_review_ref='review/1', native_checks=[{'ref': 'check/1', 'success': True}], observations={'cost': None, 'provider': 'observed-p'}))
        rows = self.call('history', dict(profile=PROFILE, model='model/v1', provider='p', effort=None))['records']
        self.assertEqual([r['outcome']['disposition'] for r in rows], ['accepted', 'repaired'])
        self.assertEqual(self.call('history', dict(profile={**PROFILE, 'language': 'Go'}))['records'], [])
        self.assertEqual(len(self.call('history', now=999999)['records']), 2)

    def test_triage_related_history_preserves_configuration_and_profile_differences(self):
        self.publish()
        token = self.call('begin', self.begin_request())['attempt_token']
        self.call('record', dict(attempt_token=token, disposition='rejected'))
        result = self.call('triage', dict(profile={**PROFILE, 'scope': 'two files'}, requirements=dict(context_tokens=100, output_tokens=500, modalities=['text'], parameters=[])))
        candidate = result['candidates'][0]
        self.assertEqual(len(candidate['history']), 1)
        self.assertEqual(candidate['history'][0]['profile']['scope'], 'one file')
        self.assertEqual(candidate['history_differences'][0], {'task_id': 'task-1', 'fields': ['scope']})

    def test_triage_bounds_repeated_evidence_without_losing_cache_or_history(self):
        metadata = json.loads(json.dumps(METADATA))
        metadata['models'][0]['public_notes'] = 'é' * 8192
        metadata['benchmarks'][0]['optional_evidence'] = [None] * 4000
        metadata['endpoints'] += [dict(metadata['endpoints'][0], provider=f'p-{index}') for index in range(127)]
        token = self.call('claim')['refresh_token']
        self.call('publish', dict(refresh_token=token, metadata=metadata))
        token = self.call('begin', self.begin_request())['attempt_token']
        self.call('record', dict(attempt_token=token, disposition='rejected', observations={'cost': None}))
        # Refresh through the CLI so its real clock also sees fresh discovery.
        claim = json.loads(self.cli('claim', {'force': True}).stdout)
        self.assertEqual(self.cli('publish', dict(refresh_token=claim['refresh_token'], metadata=metadata)).returncode, 0)
        before = self.store.read_bytes()
        history = self.call('history')
        request = dict(profile=PROFILE, requirements=dict(context_tokens=100, output_tokens=500, modalities=['text'], parameters=[]))
        with patch.object(self.api, 'incompatibilities', wraps=self.api.incompatibilities) as checked:
            result = self.call('triage', request)
        self.assertEqual(result.get('fallback'), 'response_capacity')
        self.assertEqual(result['candidates'], [])
        self.assertEqual(result['incompatible'], [])
        self.assertLess(checked.call_count, len(metadata['endpoints']))
        output = self.cli('triage', request)
        self.assertEqual(output.returncode, 0, output.stderr)
        self.assertEqual(json.loads(output.stdout), result)
        self.assertLessEqual(len(output.stdout.encode()), 1024 * 1024)
        self.assertEqual(self.store.read_bytes(), before)
        self.assertEqual(self.call('status', {'include_metadata': True})['metadata'], metadata)
        self.assertEqual(self.call('history'), history)
        self.assertEqual(self.call('begin', self.begin_request())['fallback'], 'task_already_attempted')

    def test_triage_budget_counts_exact_encoded_envelope_and_newline(self):
        metadata = json.loads(json.dumps(METADATA))
        metadata['models'][0]['public_notes'] = 'é\n'
        metadata['endpoints'].extend((dict(metadata['endpoints'][0], provider='p-2'),
                                      dict(metadata['endpoints'][0], provider='too-small', max_output_tokens=1)))
        claim = json.loads(self.cli('claim', {}).stdout)
        self.assertEqual(self.cli('publish', dict(refresh_token=claim['refresh_token'], metadata=metadata)).returncode, 0)
        query = dict(profile=PROFILE, requirements=dict(context_tokens=100, output_tokens=500, modalities=['text'], parameters=[]))
        result = self.call('triage', query)
        self.assertEqual(len(result['candidates']), 2)
        self.assertEqual(result['candidates'][0]['model_metadata'], metadata['models'][0])
        self.assertEqual(result['candidates'][0]['benchmarks'], metadata['benchmarks'])
        self.assertEqual(result['incompatible'], [dict(model='model/v1', provider='too-small', reasons=['output_tokens'])])
        expected = json.dumps(result, allow_nan=False, sort_keys=True, separators=(',', ':')) + '\n'
        output = self.cli('triage', query)
        self.assertEqual(output.returncode, 0, output.stderr)
        self.assertEqual(output.stdout, expected)
        size = len(expected.encode())
        with patch.object(self.api, 'MAX_RESPONSE', size, create=True):
            self.assertEqual(self.call('triage', query), result)
        with patch.object(self.api, 'MAX_RESPONSE', size - 1, create=True):
            self.assertEqual(self.call('triage', query).get('fallback'), 'response_capacity')

    def test_triage_bounds_history_differences_during_construction(self):
        self.publish()
        state = json.loads(self.store.read_bytes())
        state['attempts'] = [dict(model='model/v1', provider='p', profile=PROFILE, task_id=str(index)) for index in range(100)]

        class CountedProfile(dict):
            passes = 0

            def __iter__(self):
                self.passes += 1
                return super().__iter__()

        task_profile = CountedProfile(PROFILE, **{f'label-{index}': 'different' for index in range(1000)})
        query = dict(profile=task_profile, requirements=dict(context_tokens=100, output_tokens=500, modalities=['text'], parameters=[]))
        with patch.object(self.api, 'MAX_RESPONSE', 100 * 1024):
            result = self.api.triage(state, query, 100)
        self.assertEqual(result.get('fallback'), 'response_capacity')
        self.assertLess(task_profile.passes, len(state['attempts']))

    def test_concurrent_begin_dispatches_once(self):
        self.publish()
        # Publish through the CLI so its real wall clock sees fresh discovery.
        claim = json.loads(self.cli('claim', {'force': True}).stdout)
        self.assertEqual(self.cli('publish', dict(refresh_token=claim['refresh_token'], metadata=METADATA)).returncode, 0)
        request = self.begin_request()
        command = fixture_command(self, SCRIPT, 'begin', '--store', self.store, '--cwd', self.repo)
        children = [subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(8)]
        for child in children:
            child.stdin.write(json.dumps(request))
            child.stdin.close()
            child.stdin = None
        outputs = []
        for child in children:
            output, error = child.communicate(timeout=15)
            self.assertEqual(child.returncode, 0, error)
            outputs.append(json.loads(output))
        self.assertEqual(sum('attempt_token' in item for item in outputs), 1)

    def test_paid_pricing_does_not_create_a_local_credit_gate(self):
        self.publish()
        token = self.call('claim', {'force': True})['refresh_token']
        metadata = json.loads(json.dumps(METADATA))
        metadata['endpoints'][0]['pricing'] = {'prompt': '1e-9999', 'completion': '0'}
        self.call('publish', dict(refresh_token=token, metadata=metadata))
        request = self.begin_request()
        self.assertIn('attempt_token', self.call('begin', request))

    def test_status_exposes_cached_metadata_only_when_requested(self):
        self.publish()
        self.assertNotIn('metadata', self.call('status'))
        self.assertEqual(self.call('status', {'include_metadata': True}).get('metadata'), METADATA)

    def test_context_reserves_output_and_honors_separate_prompt_limit(self):
        metadata = json.loads(json.dumps(METADATA))
        metadata['endpoints'][0]['context_length'] = 1000
        token = self.call('claim')['refresh_token']
        self.call('publish', dict(refresh_token=token, metadata=metadata))
        query = dict(profile=PROFILE, requirements=dict(context_tokens=900, output_tokens=500, modalities=['text'], parameters=[]))
        self.assertEqual(self.call('triage', query)['candidates'], [])
        query['requirements']['context_tokens'] = 500
        self.assertEqual(len(self.call('triage', query)['candidates']), 1)
        token = self.call('claim', {'force': True})['refresh_token']
        metadata['endpoints'][0]['max_prompt_tokens'] = 400
        self.call('publish', dict(refresh_token=token, metadata=metadata))
        self.assertEqual(self.call('triage', query)['candidates'], [])

    def test_endpoint_parameters_cannot_be_inherited_from_model(self):
        metadata = json.loads(json.dumps(METADATA))
        metadata['models'][0]['supported_parameters'] = ['reasoning']
        del metadata['endpoints'][0]['supported_parameters']
        token = self.call('claim')['refresh_token']
        self.call('publish', dict(refresh_token=token, metadata=metadata))
        query = dict(profile=PROFILE, requirements=dict(context_tokens=100, output_tokens=500, modalities=['text'], parameters=['reasoning']))
        result = self.call('triage', query)
        self.assertEqual(result['candidates'], [])
        self.assertIn('parameter:reasoning', result['incompatible'][0]['reasons'])

    def test_begin_checks_bound_requirements_and_effort_before_consuming_task(self):
        self.publish()
        request = self.begin_request()
        request['output_tokens'] = request['requirements']['output_tokens'] = 2000
        self.assertNotIn('attempt_token', self.call('begin', request))
        request = self.begin_request()
        request['requirements']['context_tokens'] = 8900
        self.assertNotIn('attempt_token', self.call('begin', request))
        request = self.begin_request()
        request['requirements']['modalities'] = ['image']
        self.assertNotIn('attempt_token', self.call('begin', request))
        request = self.begin_request()
        request['requirements']['parameters'] = ['tools']
        self.assertNotIn('attempt_token', self.call('begin', request))
        request = self.begin_request()
        request['effort'] = 'high'
        self.assertNotIn('attempt_token', self.call('begin', self.bind_request(request)))
        request = self.begin_request()
        request['requirements']['output_tokens'] = 400
        with self.assertRaises(ValueError):
            self.call('begin', request)
        request = self.begin_request()
        del request['requirements']
        with self.assertRaises(ValueError):
            self.call('begin', request)
        self.assertEqual(self.call('history')['records'], [])
        metadata = json.loads(json.dumps(METADATA))
        metadata['models'][0]['reasoning'] = {'supported_efforts': ['low', 'high']}
        metadata['endpoints'][0]['supported_reasoning_efforts'] = ['low']
        token = self.call('claim', {'force': True})['refresh_token']
        self.call('publish', dict(refresh_token=token, metadata=metadata))
        request = self.begin_request()
        request['effort'] = 'high'
        self.assertNotIn('attempt_token', self.call('begin', self.bind_request(request)))
        request['effort'] = 'low'
        self.assertIn('attempt_token', self.call('begin', self.bind_request(request)))
        self.assertEqual(self.call('history')['records'][0]['requirements'], request['requirements'])

    def test_model_effort_values_need_explicit_endpoint_reasoning_control(self):
        metadata = json.loads(json.dumps(METADATA))
        metadata['models'][0]['reasoning'] = {'supported_efforts': ['low']}
        metadata['endpoints'][0]['supported_parameters'] = ['reasoning_effort']
        token = self.call('claim')['refresh_token']
        self.call('publish', dict(refresh_token=token, metadata=metadata))
        request = self.begin_request()
        request['effort'] = 'high'
        self.assertNotIn('attempt_token', self.call('begin', self.bind_request(request)))
        token = self.call('claim', {'force': True})['refresh_token']
        del metadata['endpoints'][0]['supported_parameters']
        self.call('publish', dict(refresh_token=token, metadata=metadata))
        request = self.begin_request()
        request['effort'] = 'low'
        self.assertNotIn('attempt_token', self.call('begin', self.bind_request(request)))
        token = self.call('claim', {'force': True})['refresh_token']
        metadata['endpoints'][0]['supported_parameters'] = ['reasoning_effort']
        self.call('publish', dict(refresh_token=token, metadata=metadata))
        request = self.begin_request()
        request['effort'] = 'low'
        self.assertIn('attempt_token', self.call('begin', self.bind_request(request)))

    def test_attempt_retains_selected_evidence_after_alias_changes(self):
        metadata = json.loads(json.dumps(METADATA))
        metadata['models'][0]['version'] = 'old-version'
        metadata['models'].append({'id': 'unrelated-model', 'version': 'irrelevant'})
        metadata['benchmarks'][0]['native_reference'] = {'model': 'native', 'score': None}
        token = self.call('claim')['refresh_token']
        self.call('publish', dict(refresh_token=token, metadata=metadata))
        token = self.call('begin', self.begin_request())['attempt_token']
        self.call('record', dict(attempt_token=token, disposition='rejected'))
        token = self.call('claim', {'force': True})['refresh_token']
        metadata['models'][0]['version'] = 'new-version'
        metadata['endpoints'][0]['context_length'] = 7000
        metadata['benchmarks'][0]['coding'] = 99
        self.call('publish', dict(refresh_token=token, metadata=metadata))
        row = self.call('history')['records'][0]
        self.assertIn('discovery_evidence', row)
        self.assertEqual(row['discovery_evidence']['model']['version'], 'old-version')
        self.assertEqual(row['discovery_evidence']['endpoint']['context_length'], 9000)
        self.assertIsNone(row['discovery_evidence']['benchmarks'][0]['coding'])
        self.assertEqual(row['discovery_evidence']['benchmarks'][0]['native_reference'], {'model': 'native', 'score': None})
        self.assertNotIn('unrelated-model', json.dumps(row))

    def test_capacity_preserves_replay_protection(self):
        self.publish()
        token = self.call('begin', self.begin_request())['attempt_token']
        self.call('record', dict(attempt_token=token, disposition='rejected'))
        with patch.object(self.api, 'MAX_ATTEMPTS', 1):
            self.assertEqual(self.call('begin', self.begin_request('other'))['fallback'], 'capacity')
        self.assertEqual(len(self.call('history')['records']), 1)
        self.assertNotIn('attempt_token', self.call('begin', self.begin_request()))

    def test_rejects_unsafe_files_repository_paths_and_oversized_non_json_input(self):
        with self.assertRaises(ValueError):
            self.api.run('status', SCRIPT.parent / 'state.json', {})
        self.store.write_text('{}')
        self.store.chmod(0o644)
        with self.assertRaises(ValueError):
            self.call('status')
        self.store.unlink()
        self.store.symlink_to(Path(self.tmp.name) / 'target')
        with self.assertRaises(ValueError):
            self.call('status')
        self.store.unlink()
        for value in ('x' * (1024 * 1024 + 1), float('nan'), object()):
            with self.assertRaises(ValueError):
                self.call('status', {'invalid': value})

    def test_cli_json_errors_and_real_process_contention(self):
        bad = self.cli('unknown', {})
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn('error', json.loads(bad.stdout))
        command = [sys.executable, '-I', '-B', str(SCRIPT), 'claim', '--store', str(self.store)]
        children = [subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(8)]
        for child in children:
            child.stdin.write('{}')
            child.stdin.close()
            child.stdin = None
        results = []
        for child in children:
            output, error = child.communicate(timeout=15)
            self.assertEqual(child.returncode, 0, error)
            results.append(json.loads(output))
        self.assertEqual(sum('refresh_token' in result for result in results), 1)
        self.assertEqual(os.stat(self.store).st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
