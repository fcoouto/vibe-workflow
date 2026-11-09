"""Private guidance-cache fixtures; no network, models or live client changes."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import hashlib
import unittest
from unittest.mock import patch

from runtime import delegation, evidence, state
from tests.fixture_delegation import AdmissionFixture


class Stage4NativeReviewTests(unittest.TestCase):
    """Synthetic native facts only; preserve the separate D5 and D6 digest domains."""
    setUp = AdmissionFixture.setUp
    request = AdmissionFixture.request
    path_claim = AdmissionFixture.path_claim
    plan_value = AdmissionFixture.plan_value
    plan = AdmissionFixture.plan
    prepare_request = AdmissionFixture.prepare_request
    raw_assignment = AdmissionFixture.raw_assignment
    worker_reconciliation_request = AdmissionFixture.worker_reconciliation_request

    def review_plan(self, **updates):
        return self.plan_value(jobs=[dict(id='review', role='review', claims=[self.path_claim(mode='read')],
            dependencies=[], max_attempts=3)], **updates)

    def review_request(self, **updates):
        request = self.prepare_request(job_id='review', dispatch_id='review-dispatch')
        request.pop('brief')
        return request | dict(coverage=['general', 'security'], criteria=['works'],
            assignment='Review app.py and callers read-only; return the exact D5 report.') | updates

    def prepared(self, **updates):
        return evidence.prepare_review(self.scope, self.review_request(**updates))

    def test_review_visibility_preserves_readonly_authority_and_digest_domains(self):
        self.plan(self.review_plan())
        visibility = dict(version=1, role='Reviewer', specialty='Parser safety', custom_role_name='Safety reviewer')
        request = self.review_request(visibility=visibility)
        try:
            prepared = evidence.prepare_review(self.scope, request)
        except state.StateError as error:
            self.fail('Independent review visibility was rejected: ' + str(error))
        binding = evidence.prepared_native_review(self.scope, prepared['attempt_id'])
        self.assertEqual(prepared['visibility'], visibility)
        self.assertEqual(binding['visibility'], visibility)
        body = json.loads(prepared['prompt'])['ownership']
        self.assertEqual(body['role'], 'review')
        self.assertTrue(all(claim['mode'] == 'read' for claim in body['claims']))
        self.assertEqual(body['request']['visibility'], visibility)
        self.assertEqual(binding['task_name'], 'review_' + prepared['attempt_id'])
        self.assertEqual(prepared['assignment_digest'], hashlib.sha256(request['assignment'].encode()).hexdigest())
        self.assertEqual(binding['ledger_assignment_digest'], prepared['ledger_assignment_digest'])
        self.assertNotEqual(binding['ledger_assignment_digest'], prepared['assignment_digest'])
        replay = evidence.prepare_review(self.scope, request)
        self.assertFalse(replay['launch_admitted'])
        self.assertEqual(replay['visibility'], visibility)
        with self.assertRaisesRegex(state.StateError, 'changed payload'):
            evidence.prepare_review(self.scope, request | dict(visibility=visibility | dict(custom_role_name='Other reviewer')))

    def observed(self, prepared, *, turn_status='completed', raw=None, **updates):
        binding = self.api.prepared_native_review_assignment(self.scope, prepared['attempt_id'])
        with patch.object(self.api, 'prepared_native_assignment', return_value=binding):
            observed = AdmissionFixture.lifecycle_observation(self, prepared, turn_status=turn_status)
        report = dict(version=1, attempt_id=prepared['attempt_id'], assignment_digest=prepared['assignment_digest'],
            candidate_digest=prepared['candidate_digest'], coverage=['general', 'security'], findings=[], limitations=[])
        observed['result'] = (json.dumps(report) if raw is None else raw) if turn_status == 'completed' else None
        observed['receipt'].update(updates)
        observed['receipt']['result_digest'] = hashlib.sha256(observed['result'].encode()).hexdigest() if observed['result'] else None
        return observed

    def observe(self, prepared, observed=None, request=None):
        request = request or self.request(attempt_id=prepared['attempt_id'], parent_turn_id='parent-turn', spawn_item_id='spawn-item')
        with patch('runtime.adapters.codex.observe_native_review_assignment', return_value=observed or self.observed(prepared)):
            return self.api.observe_assignment(self.scope, request)

    def collection(self, prepared, observed):
        r = observed['receipt']
        report = json.loads(observed['result'])
        return dict(receipt=dict(version=1, mechanism='codex-native-agent-path', native_version=r['native_version'],
            parent_session_id=r['parent_session_id'], parent_turn_id=r['parent_turn_id'], spawn_item_id=r['spawn_item_id'],
            reviewer_id=r['worker_id'], review_turn_id=r['worker_turn_id'], report_item_id=r['result_item_id'],
            assignment_digest=prepared['assignment_digest'], candidate_digest=prepared['candidate_digest'],
            report_digest=r['result_digest'], requested_model=r['spawn_observed_model'], requested_effort=r['spawn_observed_effort'],
            task_name=r['task_name'], agent_path=r['agent_path']), report=report)

    def collect(self, prepared, observation):
        with patch('runtime.adapters.codex.collect_native_review', return_value=observation):
            return evidence.collect_review(self.scope, self.request(attempt_id=prepared['attempt_id'],
                parent_turn_id='parent-turn', spawn_item_id='spawn-item'))

    def test_review_requires_explicit_native_job_and_atomic_pair(self):
        with self.assertRaises(state.StateError):
            evidence.prepare_review(self.scope, self.request(coverage=['general'], criteria=['works'], assignment='Unmanaged review'))
        self.plan(self.review_plan())
        for delta in (dict(job_id='missing'), dict(selection=self.review_request()['selection'] | dict(model='absent'))):
            with self.subTest(delta=delta), self.assertRaises(state.StateError):
                self.prepared(**delta)
        with patch.object(state, '_save', side_effect=state.StateError('fixture atomic failure')):
            with self.assertRaises(state.StateError):
                self.prepared()
        with state._database(self.scope) as db:
            self.assertEqual(self.api._ledger(self.scope, db), [])
            self.assertEqual(state._task(db, state._scope_id(self.scope), 'codex', 'parent')['evidence']['attempts'], [])
        request = self.review_request()
        # A concurrent loser either replays or is rejected by whichever lock it meets first: the
        # reservation guard ('busy'), or, under load, a 0.1 s SQLite lock on artifact registration or
        # task state. Each rejection must leave no reservation of its own; a retry must replay.
        contention = ('D4 transaction busy', 'Evidence authority registration unavailable or locked',
                      'Runtime state is unavailable, corrupt, locked or incompatible')
        rejected = []
        def reserve(_):
            try:
                return evidence.prepare_review(self.scope, request)
            except state.StateError as error:
                rejected.append(str(error))
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            admitted = list(pool.map(reserve, range(2)))
        for message in rejected:
            self.assertTrue(any(text in message for text in contention), message)
        self.assertEqual(sum(item['launch_admitted'] for item in admitted if item), 1)
        with state._database(self.scope) as db:
            ledger = self.api._ledger(self.scope, db)
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
        self.assertEqual(len(ledger), 1)
        self.assertEqual([attempt['id'] for attempt in task['evidence']['attempts']], [ledger[0]['id']])
        self.assertEqual(task['revision'], request['expected_revision'] + 1)
        retried = [item or evidence.prepare_review(self.scope, request) for item in admitted]
        self.assertEqual({item['attempt_id'] for item in retried}, {ledger[0]['id']})
        self.assertEqual(sum(item['launch_admitted'] for item in retried), 1)
        with state._database(self.scope) as db:
            self.assertEqual(len(self.api._ledger(self.scope, db)), 1)

    def test_review_pair_digests_and_replay_precede_discovery_and_candidate(self):
        self.plan(self.review_plan())
        request = self.review_request()
        prepared = evidence.prepare_review(self.scope, request)
        self.assertTrue(prepared['launch_admitted'])
        self.assertFalse(prepared['replayed'])
        self.assertEqual(prepared['revision'], request['expected_revision'] + 1)
        binding = self.api.prepared_native_review_assignment(self.scope, prepared['attempt_id'])
        self.assertEqual(set(binding), {'attempt_id','parent_session_id','task_name','assignment_digest','candidate_digest',
            'requested_model','requested_effort','client_surface','native_version','pins','accepted_identity','scope_id'})
        self.assertEqual(binding['task_name'], 'review_' + prepared['attempt_id'])
        self.assertEqual(prepared['assignment_digest'], hashlib.sha256(request['assignment'].encode()).hexdigest())
        self.assertEqual(binding['assignment_digest'], prepared['ledger_assignment_digest'])
        self.assertNotEqual(prepared['assignment_digest'], binding['assignment_digest'])
        self.assertIn(request['assignment'], prepared['prompt'])
        legacy = evidence.prepared_native_review(self.scope, prepared['attempt_id'])
        self.assertEqual(legacy['assignment_digest'], prepared['assignment_digest'])
        with patch.object(evidence, 'capture_candidate', side_effect=AssertionError('replay captured')), \
             patch('runtime.adapters.codex.discover_native_workers', side_effect=AssertionError('replay discovered')), \
             patch.object(self.api, '_now', return_value=10**12):
            replay = evidence.prepare_review(self.scope, request)
        self.assertFalse(replay['launch_admitted'])
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['attempt_id'], prepared['attempt_id'])
        with self.assertRaises(state.StateError):
            evidence.prepare_review(self.scope, request | dict(assignment='Changed request'))
        status = self.api.delegation_status(self.scope, 'codex', 'parent')
        self.assertEqual(status['budgets']['attempts_consumed'], 1)
        self.assertEqual(status['budgets']['resource_units_consumed'], 1)
        with state._database(self.scope) as db:
            attempts = state._task(db, state._scope_id(self.scope), 'codex', 'parent')['evidence']['attempts']
        self.assertEqual([a['id'] for a in attempts], [prepared['attempt_id']])

    def test_v1_review_collection_matches_linked_lifecycle_and_plaintext_assignment(self):
        self.plan(self.review_plan())
        prepared = self.prepared()
        binding = evidence.prepared_native_review(self.scope, prepared['attempt_id'])
        self.assertEqual(binding['ledger_assignment_digest'], prepared['ledger_assignment_digest'])
        self.assertEqual(binding['assignment_digest'], prepared['assignment_digest'])
        self.assertNotEqual(binding['ledger_assignment_digest'], binding['assignment_digest'])
        observed = self.observed(prepared, mechanism='codex-native-collab-call', agent_path=None)
        self.assertEqual(observed['receipt']['assignment_digest'], binding['ledger_assignment_digest'])
        self.assertEqual(self.observe(prepared, observed)['status'], 'observed')
        collection = self.collection(prepared, observed)
        collection['receipt']['mechanism'] = 'codex-native-collab-call'
        self.assertEqual(collection['receipt']['assignment_digest'], binding['assignment_digest'])
        for key, value in (('mechanism', 'codex-native-agent-path'), ('task_name', 'review_other'),
                           ('reviewer_id', 'other-child'), ('parent_turn_id', 'other-parent-turn'),
                           ('review_turn_id', 'other-turn'), ('report_item_id', 'other-report'),
                           ('report_digest', 'f' * 64), ('candidate_digest', 'f' * 64),
                           ('assignment_digest', binding['ledger_assignment_digest'])):
            bad = deepcopy(collection); bad['receipt'][key] = value
            with self.subTest(key=key), self.assertRaises(state.StateError):
                self.collect(prepared, bad)
        bad = deepcopy(collection); bad['report']['assignment_digest'] = binding['ledger_assignment_digest']
        with self.assertRaises(state.StateError):
            self.collect(prepared, bad)
        with state._database(self.scope) as db:
            record = next(row for row in self.api._ledger(self.scope, db) if row['id'] == prepared['attempt_id'])
        path_history = deepcopy(self.api._worker_history(self.scope, record['data']['observation']))
        native_path = '/root/' + observed['receipt']['task_name']
        path_history[0][1]['receipt']['agent_path'] = native_path
        matching = deepcopy(collection); matching['receipt']['agent_path'] = native_path
        # Isolate route correspondence with identical valid paths; retain real null-path history below.
        with patch.object(self.api, '_worker_history', return_value=path_history):
            self.assertTrue(evidence._review_quality_matches(self.scope, prepared['attempt'], record, matching))
            mismatched = deepcopy(matching); mismatched['receipt']['mechanism'] = 'codex-native-agent-path'
            with self.subTest(case='mechanism only with identical valid paths'), self.assertRaises(state.StateError):
                self.collect(prepared, mismatched)
        self.assertEqual(self.collect(prepared, collection)['attempt']['status'], 'collected')
        self.assertNotEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'settled')
        assessed = evidence.assess_review(self.scope, self.request(attempt_id=prepared['attempt_id'], assessment=dict(
            coverage=['general', 'security'], criteria=['works'], qualification='Independent fixture expertise',
            independence='Separate observed fixture reviewer', semantic_coverage='Read requirements and code',
            dispositions=[], decision_refs=['user:accepted'])))
        self.assertEqual(assessed['attempt']['status'], 'assessed')
        self.assertNotEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'settled')

    def test_review_collection_correlates_lifecycle_and_assessment_never_settles(self):
        self.plan(self.review_plan())
        prepared = self.prepared()
        observed = self.observed(prepared)
        first = self.observe(prepared, observed)
        self.assertEqual(first['status'], 'observed')
        repeat = self.observe(prepared, observed)
        self.assertTrue(repeat['replayed'])
        self.assertEqual(repeat['revision'], first['revision'])
        collection = self.collection(prepared, observed)
        for key in ('reviewer_id', 'review_turn_id', 'report_item_id', 'report_digest', 'spawn_item_id'):
            bad = deepcopy(collection); bad['receipt'][key] = 'wrong'
            with self.subTest(key=key), self.assertRaises(state.StateError):
                self.collect(prepared, bad)
        result = self.collect(prepared, collection)
        self.assertEqual(result['attempt']['status'], 'collected')
        assessed = evidence.assess_review(self.scope, self.request(attempt_id=prepared['attempt_id'], assessment=dict(
            coverage=['general', 'security'], criteria=['works'], qualification='Independent fixture expertise',
            independence='Separate observed reviewer', semantic_coverage='Read requirements and code', dispositions=[],
            decision_refs=['user:accepted'])))
        self.assertEqual(assessed['attempt']['status'], 'assessed')
        self.assertNotEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'settled')
        self.assertFalse(evidence.verify_completion(self.scope, self.request(purpose='completion'))['verified'])
        request = self.worker_reconciliation_request(prepared, changed_paths=[])
        self.assertEqual(self.api.reconcile_assignment(self.scope, request)['outcome'], 'completed')
        self.assertTrue(self.api.reconcile_assignment(self.scope, request)['replayed'])
        with patch('runtime.adapters.codex.discover_native_workers', side_effect=AssertionError('settled replay discovered')):
            replay = evidence.prepare_review(self.scope, self.review_request() | dict(expected_revision=1))
        self.assertFalse(replay['launch_admitted'])
        self.assertEqual(replay['attempt']['status'], 'assessed')

    def test_review_malformed_incomplete_and_mutated_results_remain_recoverable(self):
        self.plan(self.review_plan())
        previous = None
        for number, kind in enumerate(('malformed', 'incomplete', 'mutated')):
            with self.subTest(kind=kind):
                extra = dict(dispatch_id='review-' + str(number))
                if previous:
                    extra.update(retry_of=previous['attempt_id'], diagnostic=dict(prior_attempt_id=previous['attempt_id'],
                        cause='Fixture prior failed review', change='New bounded review', evidence=['fixture:new-' + kind]))
                prepared = self.prepared(**extra)
                observed = self.observed(prepared, raw='malformed') if kind == 'malformed' else self.observed(prepared,
                    turn_status='inProgress' if kind == 'incomplete' else 'completed')
                self.observe(prepared, observed)
                if kind == 'mutated':
                    (self.repo / 'app.py').write_text('value = 2\n')
                    with self.assertRaises(state.StateError):
                        self.collect(prepared, self.collection(prepared, observed))
                elif kind == 'malformed':
                    self.assertEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'observed')
                else:
                    self.assertIsNone(observed['result'])
                request = self.worker_reconciliation_request(prepared, outcome='failed',
                    changed_paths=['app.py'] if kind == 'mutated' else [])
                bad = self.worker_reconciliation_request(prepared, outcome='failed', proof_delta=dict(descendants_stopped=False))
                with self.assertRaises(state.StateError):
                    self.api.reconcile_assignment(self.scope, bad)
                self.assertEqual(self.api.reconcile_assignment(self.scope, request)['outcome'], 'failed')
                previous = prepared
        with self.assertRaises(state.StateError):
            self.prepared(dispatch_id='fourth', retry_of=previous['attempt_id'], diagnostic=dict(
                prior_attempt_id=previous['attempt_id'], cause='Original exhausted', change='Cannot reset', evidence=['fixture:new-fourth']))
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 3)

    def test_review_contradictory_marker_and_cancelled_never_started_claims(self):
        self.plan(self.review_plan())
        prepared = self.prepared()
        wrong = self.observed(prepared, task_name='worker_' + prepared['attempt_id'])
        self.assertEqual(self.observe(prepared, wrong)['status'], 'uncertain')
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(prepared))
        good = self.observed(prepared)
        self.observe(prepared, good)
        conflict = self.observed(prepared, worker_id='different-child')
        self.assertEqual(self.observe(prepared, conflict)['status'], 'uncertain')
        request = self.worker_reconciliation_request(prepared, outcome='failed', changed_paths=[])
        self.api.reconcile_assignment(self.scope, request)
        next_review = self.prepared(dispatch_id='never-started', retry_of=prepared['attempt_id'], diagnostic=dict(
            prior_attempt_id=prepared['attempt_id'], cause='Contradictory review', change='New attempt', evidence=['fixture:diagnosed']))
        state.update_task(self.scope, self.request(state='CANCELLED', reason='User cancelled launch', reference='user:cancel'))
        task = state.task_status(self.scope, 'codex', 'parent')['task']
        with self.assertRaises(state.StateError):
            state.start_task(self.scope, self.identity | dict(task_id='new', mode='editable', decision_ref='user:accepted',
                replaces=dict(task_id='task', expected_revision=task['revision'])))
        request = self.worker_reconciliation_request(next_review, outcome='cancelled', changed_paths=[])
        self.assertEqual(self.api.reconcile_assignment(self.scope, request)['outcome'], 'cancelled')
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 2)

    def test_review_job_is_readonly_and_worker_entry_cannot_admit_it(self):
        bad = self.review_plan(); bad['jobs'][0]['claims'][0]['mode'] = 'write'
        with self.assertRaises(state.StateError):
            self.plan(bad)
        self.plan(self.review_plan())
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, self.prepare_request(job_id='review'))
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['assignments'], [])

    def test_review_dependencies_require_settled_checks_without_native_consumption(self):
        plan = self.review_plan()
        plan['jobs'].append(dict(id='unit', role='check', claims=[self.path_claim(mode='read')], dependencies=[], max_attempts=1))
        plan['jobs'][0]['dependencies'] = ['unit']
        self.plan(plan)
        with self.assertRaises(state.StateError):
            self.prepared()
        checked = self.fixture.check()
        with self.assertRaises(state.StateError):
            self.prepared()
        self.fixture.reconcile(checked)
        prepared = self.prepared()
        self.assertTrue(prepared['launch_admitted'])
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 1)

    def test_planned_check_requires_its_worker_before_reservation(self):
        plan = self.plan_value()
        plan['jobs'].append(dict(id='unit', role='check', claims=[self.path_claim(mode='read')],
            dependencies=['edit'], max_attempts=1))
        self.plan(plan)
        with patch.object(evidence, '_execute', side_effect=AssertionError('check ran without its planned worker')):
            with self.assertRaisesRegex(state.StateError, 'dependency prerequisite'):
                self.fixture.check()
        task = state.task_status(self.scope, 'codex', 'parent')['task']
        self.assertEqual(task['evidence']['attempts'], [])
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['assignments'], [])

    def test_review_preserves_plain_assignment_limit_without_relaxing_external_request_cap(self):
        self.plan(self.review_plan())
        assignment = 'r' * 65536
        prepared = self.prepared(assignment=assignment)
        self.assertEqual(prepared['assignment_digest'], hashlib.sha256(assignment.encode()).hexdigest())
        prompt = json.loads(prepared['prompt'])
        self.assertEqual(prompt['assignment'], assignment)
        self.assertEqual(prompt['report_contract']['assignment_digest'], prepared['assignment_digest'])
        self.assertEqual(prompt['report_contract']['attempt_id'], prepared['attempt_id'])
        with self.assertRaises(state.StateError):
            self.prepared(assignment='r' * 65537)
        with self.assertRaises(state.StateError):
            self.prepared(assignment='😀' * 65536)

    def test_review_original_round_limit_survives_changed_evidence_plan(self):
        plan = evidence._load_plan(self.scope, self.identity)
        plan['review_limit'] = 1
        evidence.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:one-original-review'))
        self.plan(self.review_plan())
        first = self.prepared()
        self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(first, outcome='cancelled', changed_paths=[]))
        plan['review_limit'] = 3
        evidence.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:cannot-reset-history'))
        with self.assertRaisesRegex(state.StateError, 'Original review round limit'):
            self.prepared(dispatch_id='reset-attempt', retry_of=first['attempt_id'], diagnostic=dict(
                prior_attempt_id=first['attempt_id'], cause='First round cancelled', change='Try changed evidence plan',
                evidence=['fixture:new-diagnosis']))
        status = self.api.delegation_status(self.scope, 'codex', 'parent')
        self.assertEqual(status['budgets']['attempts_consumed'], 1)
        self.assertEqual(len(status['assignments']), 1)

    def assess_current_review(self, prepared, **updates):
        return evidence.assess_review(self.scope, self.request(attempt_id=prepared['attempt_id'], assessment=dict(
            coverage=['general', 'security'], criteria=['works'], qualification='Independent fixture expertise',
            independence='Separate observed reviewer', semantic_coverage='Read requirements and code', dispositions=[],
            decision_refs=['user:accepted']) | updates))

    def quality_review_fixture(self, findings=None):
        self.fixture.checked()
        self.plan(self.review_plan())
        prepared = self.prepared()
        first = self.observed(prepared)
        if findings:
            report = json.loads(first['result']); report['findings'] = findings
            first['result'] = json.dumps(report)
            first['receipt']['result_digest'] = hashlib.sha256(first['result'].encode()).hexdigest()
        self.observe(prepared, first)
        self.collect(prepared, self.collection(prepared, first))
        assessed = self.assess_current_review(prepared)
        return prepared, assessed['attempt']

    def test_later_malformed_review_cannot_settle_completed_using_previous_quality(self):
        prepared, original = self.quality_review_fixture()
        later = self.observed(prepared, raw='malformed later result', worker_turn_id='later-turn', result_item_id='later-final')
        self.observe(prepared, later)
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(prepared, changed_paths=[]))
        with self.assertRaises(state.StateError):
            self.assess_current_review(prepared)
        recovery = self.api.reconcile_assignment(self.scope,
            self.worker_reconciliation_request(prepared, outcome='failed', changed_paths=[]))
        self.assertEqual(recovery['outcome'], 'failed')
        verified = evidence.verify_completion(self.scope, self.request(purpose='completion'))
        self.assertFalse(verified['verified'])
        current = next(a for a in verified['task']['evidence']['attempts'] if a['id'] == prepared['attempt_id'])
        self.assertEqual(current['collected'], original['collected'])
        self.assertEqual(current['assessment'], original['assessment'])

    def test_later_interrupted_review_release_does_not_reuse_previous_quality(self):
        prepared, original = self.quality_review_fixture()
        self.observe(prepared, self.observed(prepared, turn_status='interrupted', worker_turn_id='later-turn'))
        recovery = self.api.reconcile_assignment(self.scope,
            self.worker_reconciliation_request(prepared, outcome='failed', changed_paths=[]))
        self.assertEqual(recovery['outcome'], 'failed')
        verified = evidence.verify_completion(self.scope, self.request(purpose='completion'))
        self.assertFalse(verified['verified'])
        self.assertTrue(any('qualified review is missing or stale' in b for b in verified['blockers']))
        current = next(a for a in verified['task']['evidence']['attempts'] if a['id'] == prepared['attempt_id'])
        self.assertEqual(current['collected'], original['collected'])
        self.assertEqual(current['assessment'], original['assessment'])
        with self.assertRaises(state.StateError):
            self.assess_current_review(prepared)

    def test_matching_later_uncollected_review_can_assess_before_delayed_stop(self):
        self.fixture.checked()
        self.plan(self.review_plan())
        prepared = self.prepared()
        self.observe(prepared)
        later = self.observed(prepared, worker_turn_id='later-turn', result_item_id='later-final')
        self.observe(prepared, later)
        self.collect(prepared, self.collection(prepared, later))
        self.assess_current_review(prepared)
        self.assertFalse(evidence.verify_completion(self.scope, self.request(purpose='completion'))['verified'])
        self.assertEqual(self.api.reconcile_assignment(self.scope,
            self.worker_reconciliation_request(prepared, changed_paths=[]))['outcome'], 'completed')
        self.assertTrue(evidence.verify_completion(self.scope, self.request(purpose='completion'))['verified'])
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 1)

    def test_superseded_review_findings_survive_failed_ownership_recovery(self):
        finding = dict(id='security-prior', severity='high', scope='security', status='confirmed',
            location='app.py:1', scenario='Reachable fixture unsafe path', evidence='Independent code tracing',
            counterevidence='No applicable guard')
        prepared, original = self.quality_review_fixture([finding])
        later = self.observed(prepared, worker_turn_id='later-turn', result_item_id='later-final')
        self.observe(prepared, later)
        with self.assertRaisesRegex(state.StateError, 'not pending'):
            self.collect(prepared, self.collection(prepared, later))
        self.api.reconcile_assignment(self.scope,
            self.worker_reconciliation_request(prepared, outcome='failed', changed_paths=[]))
        verified = evidence.verify_completion(self.scope, self.request(purpose='completion'))
        self.assertFalse(verified['verified'])
        self.assertTrue(any('security finding needs fresh scoped review' in b for b in verified['blockers']))
        self.assertTrue(any('qualified review is missing or stale' in b for b in verified['blockers']))
        self.assertEqual(evidence._read(self.scope, original['collected'])['report']['findings'], [finding])
        fresh = self.prepared(dispatch_id='fresh-quality', retry_of=prepared['attempt_id'], diagnostic=dict(
            prior_attempt_id=prepared['attempt_id'], cause='Prior review superseded', change='Explicit fresh review',
            evidence=['fixture:fresh-quality-diagnosis']))
        observed = self.observed(fresh)
        self.observe(fresh, observed)
        self.collect(fresh, self.collection(fresh, observed))
        self.assess_current_review(fresh, rechecked_findings=[dict(attempt_id=prepared['attempt_id'],
            finding_id=finding['id'], status='resolved', reference='fixture:fresh-independent-inspection')])
        self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(fresh, changed_paths=[]))
        self.assertTrue(evidence.verify_completion(self.scope, self.request(purpose='completion'))['verified'])

    def test_lost_committed_review_response_keeps_pair_budget_and_current_task(self):
        self.plan(self.review_plan())
        request = self.review_request()
        committed = []
        prepare_native = delegation._prepare_native
        def lose_response(scope, original_request, *, role):
            response = prepare_native(scope, original_request, role=role)
            self.assertTrue(response['launch_admitted'])
            with state._database(scope) as visible:
                records = self.api._ledger(scope, visible)
                record = next(r for r in records if r['id'] == response['attempt']['id'])
                self.assertEqual([r['id'] for r in records], [record['id']])
                task = state._task(visible, state._scope_id(scope), 'codex', 'parent')
            self.assertEqual(task['revision'], request['expected_revision'] + 1)
            self.assertEqual(task['delegation']['attempts_consumed'], 1)
            self.assertEqual(task['delegation']['resource_units_consumed'], 1)
            attempts = task['evidence']['attempts']
            self.assertEqual([a['id'] for a in attempts], [record['id']])
            self.assertEqual(attempts[0]['ledger_assignment_digest'], record['data']['assignment']['digest'])
            self.assertEqual(attempts[0]['assignment_digest'], hashlib.sha256(request['assignment'].encode()).hexdigest())
            state.update_task(scope, self.identity | dict(expected_revision=task['revision'], state='BLOCKED',
                reason='Coordinator update after durable admission', reference='user:hold'))
            with state._database(scope) as visible:
                committed.append(deepcopy(state._task(visible, state._scope_id(scope), 'codex', 'parent')))
            raise RuntimeError('fixture committed review response lost')
        with patch.object(delegation, '_prepare_native', side_effect=lose_response):
            with self.assertRaisesRegex(RuntimeError, 'fixture committed review response lost'):
                evidence.prepare_review(self.scope, request)
        with patch('runtime.adapters.codex.discover_native_workers', side_effect=AssertionError('replay discovered')), \
             patch.object(evidence, 'capture_candidate', side_effect=AssertionError('replay captured')):
            replay = evidence.prepare_review(self.scope, request)
        self.assertFalse(replay['launch_admitted'])
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['task'], committed[0])
        self.assertEqual(replay['task']['state'], 'BLOCKED')
        self.assertEqual(replay['attempt']['id'], committed[0]['evidence']['attempts'][0]['id'])
        self.assertEqual(replay['task']['delegation']['attempts_consumed'], 1)
        self.assertEqual(replay['task']['delegation']['resource_units_consumed'], 1)


    def test_review_collection_keeps_admitted_version_across_observer_updates(self):
        self.plan(self.review_plan())
        prepared = self.prepared()
        binding = evidence.prepared_native_review(self.scope, prepared['attempt_id'])
        self.assertEqual(binding['native_version'], '0.163.0')
        self.assertEqual(binding.get('client_surface'), 'codex-cli')
        observed = self.observed(prepared, current_observer_version='0.165.0',
            parent_creation_version='0.164.0-alpha.11.1', worker_creation_version='0.164.0-alpha.11.1')
        self.assertEqual(self.observe(prepared, observed)['status'], 'observed')
        collection = self.collection(prepared, observed)
        collection['receipt'].update(current_observer_version='0.166.0', parent_creation_version='0.164.0-alpha.11.1',
            worker_creation_version='0.164.0-alpha.11.1')
        result = self.collect(prepared, collection)
        receipt = evidence._read(self.scope, result['attempt']['collected'])['receipt']
        self.assertEqual(receipt['native_version'], '0.163.0')
        self.assertEqual(receipt['current_observer_version'], '0.166.0')


if __name__ == '__main__':
    unittest.main()
