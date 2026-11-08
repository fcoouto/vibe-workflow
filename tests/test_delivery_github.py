"""Provider observations preserve unknowns and never manufacture merge authority. Checks transport fixtures: no credentials, network, or readiness authority. Low-level provider fixtures; transport success is not operation authority."""
from copy import deepcopy
import importlib.util
import json
import subprocess
import sys
import unittest
from urllib.parse import unquote

from runtime.adapters.github_checks import ChecksError, GitHubChecks
from runtime.adapters.github_delivery import GitHubDelivery


class GitHubDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.publication = dict(host='github.com', repository='owner/repo', target='main',
                                branch='codex/feature', pull_request=42)
        self.repo = dict(id=123, full_name='owner/repo', default_branch='main')
        self.pr = dict(number=42, state='open', draft=True, merged=False, merged_at=None,
                       merge_commit_sha=None, mergeable=None,
                       base=dict(ref='main', sha='b' * 40, repo=self.repo),
                       head=dict(ref='codex/feature', sha='a' * 40, repo=self.repo))
        self.calls = []

    def adapter(self, *, code=0, raw=None):
        self.assertIsNotNone(importlib.util.find_spec('runtime.adapters.github_delivery'),
                             'GitHub delivery observation is missing')
        from runtime.adapters.github_delivery import GitHubDelivery

        def run(argv, **bounds):
            self.calls.append((argv, bounds))
            value = self.pr if argv[-1].endswith('/pulls/42') else self.repo
            return code, json.dumps(value).encode() if raw is None else raw

        return GitHubDelivery(executable=sys.executable, host='github.com', run=run)

    def test_open_pr_observation_retains_unknown_checks_and_reviews(self):
        result = self.adapter().observe(self.publication)
        self.assertEqual(result['repository_id'], 123)
        self.assertEqual(result['head'], 'a' * 40)
        self.assertFalse(result['merged'])
        self.assertEqual(result['checks'], 'unobserved')
        self.assertEqual(result['reviews'], 'unobserved')
        self.assertIsNone(result['mergeability'])
        self.assertNotIn('merge_authorized', result)
        for argv, bounds in self.calls:
            self.assertEqual(argv[:4], [sys.executable, 'api', '--hostname', 'github.com'])
            self.assertIn('X-GitHub-Api-Version: 2022-11-28', argv)
            self.assertEqual(bounds, dict(timeout=10, limit=128 * 1024))

    def test_merged_pr_records_result_without_requiring_head_ancestry(self):
        self.pr.update(state='closed', draft=False, merged=True,
                       merged_at='2026-11-02T12:00:00Z', merge_commit_sha='c' * 40)
        result = self.adapter().observe(self.publication)
        self.assertTrue(result['merged'])
        self.assertEqual(result['merge_commit'], 'c' * 40)
        self.assertNotEqual(result['head'], result['merge_commit'])

    def test_wrong_host_or_repository_text_is_rejected_before_execution(self):
        for update in (dict(host='other.example'), dict(repository='owner/repo?secret=x'),
                       dict(repository='--help'), dict(pull_request=True), dict(pull_request=0)):
            self.calls.clear()
            with self.assertRaises(ValueError):
                self.adapter().observe(self.publication | update)
            self.assertEqual(self.calls, [])

    def test_wrong_base_fork_or_changed_branch_is_rejected(self):
        original = deepcopy(self.pr)
        for location, update in (('base', dict(ref='other')), ('head', dict(ref='other')),
                                 ('head', dict(repo=self.repo | dict(id=999)))):
            self.pr = deepcopy(original)
            self.pr[location].update(update)
            with self.assertRaises(ValueError):
                self.adapter().observe(self.publication)

    def test_incomplete_merge_and_boolean_repository_id_are_rejected(self):
        self.pr.update(merged=True, merged_at=None)
        with self.assertRaises(ValueError):
            self.adapter().observe(self.publication)
        self.repo['id'] = True
        with self.assertRaises(ValueError):
            self.adapter().observe(self.publication)

    def test_unavailable_or_oversized_response_never_becomes_absence(self):
        for code, raw in ((1, b'private-token-do-not-echo'), (0, b' ' * (128 * 1024 + 1)),
                          (0, b'{}'), (0, b'not JSON')):
            with self.subTest(code=code), self.assertRaises(ValueError) as error:
                self.adapter(code=code, raw=raw).observe(self.publication)
            self.assertNotIn('private-token', str(error.exception))

    def test_invalid_expected_refs_are_rejected_before_execution(self):
        for update in (dict(target=None), dict(branch=None), dict(target='--help'),
                       dict(branch='bad..branch'), dict(target='refs/heads/../main')):
            self.calls.clear()
            with self.assertRaises(ValueError):
                self.adapter().observe(self.publication | update)
            self.assertEqual(self.calls, [])

    def test_standard_subprocess_timeout_is_sanitized(self):
        import subprocess
        adapter = self.adapter()

        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired('private-token-do-not-echo', 10, output=b'private-output')

        adapter.run = timeout
        with self.assertRaises(ValueError) as error:
            adapter.observe(self.publication)
        self.assertNotIn('private-token', str(error.exception))


class GitHubChecksTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.responses = []
        self.head = 'a' * 40
        self.binding = dict(head_sha=self.head, name='workflow/final', external_id='operation:one')
        self.repo = dict(id=20, full_name='owner/repo')
        self.run = dict(id=40, app=dict(id=30), head_sha=self.head, name='workflow/final',
                        external_id='operation:one', status='in_progress', conclusion=None)
        self.api = GitHubChecks(repository='owner/repo', repository_id=20, app_id=30, request=self.request)

    def request(self, method, endpoint, *, payload, timeout, limit):
        self.calls.append((method, endpoint, deepcopy(payload), timeout, limit))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        status, value = response
        return status, value if isinstance(value, bytes) else json.dumps(value).encode()

    def queue(self, *responses):
        self.responses.extend(responses)

    def test_create_binds_actual_repository_app_head_and_owned_run(self):
        self.queue((200, self.repo), (201, self.run))
        receipt = self.api.create(**self.binding)
        self.assertEqual(receipt['id'], 40)
        self.assertEqual(receipt['repository_id'], 20)
        self.assertEqual(receipt['app_id'], 30)
        self.assertFalse(receipt['merge_authority'])
        self.assertEqual(self.calls[1][:3], ('POST', '/repos/owner/repo/check-runs',
            dict(self.binding, status='in_progress')))
        self.assertTrue(all(row[3:] == (10, 128 * 1024) for row in self.calls))

    def test_complete_reobserves_owned_run_before_patch(self):
        completed = dict(self.run, status='completed', conclusion='success')
        self.queue((200, self.repo), (200, self.run), (200, completed))
        receipt = self.api.complete(run_id=40, conclusion='success', **self.binding)
        self.assertEqual(receipt['conclusion'], 'success')
        self.assertEqual([row[:2] for row in self.calls], [('GET', '/repos/owner/repo'),
            ('GET', '/repos/owner/repo/check-runs/40'), ('PATCH', '/repos/owner/repo/check-runs/40')])
        self.assertEqual(self.calls[-1][2]['status'], 'completed')
        self.assertNotIn('ready', receipt)
        self.assertFalse(receipt['merge_authority'])

    def test_wrong_repository_or_run_identity_prevents_write(self):
        for field, value in [('id', 21), ('full_name', 'other/repo')]:
            with self.subTest(field=field):
                self.calls.clear()
                self.responses = [(200, dict(self.repo, **{field: value}))]
                with self.assertRaises(ChecksError):
                    self.api.create(**self.binding)
                self.assertEqual(len(self.calls), 1)
        for field, value in [('id', 41), ('app', dict(id=31)), ('head_sha', 'b' * 40),
                             ('external_id', 'operation:other'), ('name', 'other')]:
            with self.subTest(field=field):
                self.calls.clear()
                self.responses = [(200, self.repo), (200, dict(self.run, **{field: value}))]
                with self.assertRaises(ChecksError):
                    self.api.complete(run_id=40, conclusion='success', **self.binding)
                self.assertFalse(any(row[0] == 'PATCH' for row in self.calls))

    def test_invalid_selectors_and_conclusions_do_not_call_transport(self):
        for options in [dict(self.binding, head_sha='bad'), dict(self.binding, external_id=''),
                        dict(self.binding, name='private\nname')]:
            with self.subTest(options=options), self.assertRaises(ChecksError):
                self.api.create(**options)
        with self.assertRaises(ChecksError):
            self.api.complete(run_id=True, conclusion='success', **self.binding)
        with self.assertRaises(ChecksError):
            self.api.complete(run_id=40, conclusion='neutral', **self.binding)
        self.assertEqual(self.calls, [])
        with self.assertRaises(ChecksError):
            GitHubChecks(repository='../repo', repository_id=20, app_id=30, request=self.request)
        with self.assertRaises(ChecksError):
            GitHubChecks(repository='owner/repo', repository_id=20, app_id=30, request=None)

    def test_transport_failure_is_sanitized_without_retry(self):
        self.queue((200, self.repo), RuntimeError('PRIVATE_TOKEN_AND_RESPONSE'))
        with self.assertRaises(ChecksError) as caught:
            self.api.create(**self.binding)
        self.assertNotIn('PRIVATE', str(caught.exception))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.responses, [])

    def test_response_bounds_duplicate_json_and_wrong_create_result_reject(self):
        for result in [(201, b'x' * (128 * 1024 + 1)),
                       (201, b'{"id":40,"id":41}'),
                       (201, dict(self.run, head_sha='b' * 40)), (403, self.run)]:
            with self.subTest(result=result[0]):
                self.responses = [(200, self.repo), result]
                with self.assertRaises(ChecksError):
                    self.api.create(**self.binding)

    def test_reconcile_uncertain_create_never_creates_or_selects_foreign_app(self):
        foreign = dict(self.run, id=41, app=dict(id=31))
        self.queue((200, self.repo), (200, dict(total_count=2, check_runs=[foreign, self.run])))
        receipt = self.api.reconcile(**self.binding)
        self.assertEqual(receipt['id'], 40)
        self.assertTrue(all(row[0] == 'GET' for row in self.calls))
        self.calls.clear()
        self.queue((200, self.repo), (200, dict(total_count=0, check_runs=[])))
        self.assertIsNone(self.api.reconcile(**self.binding))
        self.assertTrue(all(row[0] == 'GET' for row in self.calls))

    def test_reconcile_incomplete_or_ambiguous_listing_rejects(self):
        for listing in [dict(total_count=2, check_runs=[self.run]),
                        dict(total_count=101, check_runs=[]),
                        dict(total_count=2, check_runs=[self.run, dict(self.run, id=41)])]:
            self.responses = [(200, self.repo), (200, listing)]
            with self.assertRaises(ChecksError):
                self.api.reconcile(**self.binding)


class ProviderMutationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = GitHubDeliveryTests('runTest')
        self.fixture.setUp()
        self.pr = self.fixture.pr
        self.pr.update(title='Human title', body='Human introduction')
        self.exists, self.lost_create, self.failed_undo, self.next_page = True, False, False, False
        self.merge_race, self.metadata_race, self.target_race, self.lost_comment = False, False, False, False
        self.calls, self.creates, self.comments, self.labels, self.known_labels = [], 0, [], set(), set()
        self.adapter = GitHubDelivery(executable=sys.executable, host='github.com', run=self.transport_run,
            mutate=self.transport_run, merge=self.fenced_merge)

    def fenced_merge(self, argv, **bounds):
        # Software-only target fence. Ordinary GitHub REST supplies no such guarantee.
        expected = bounds.pop('preconditions')['binding']
        if self.target_race:
            self.pr['base']['ref'] = 'release'
            self.target_race = False
        actual = dict(host='github.com', repository=self.fixture.repo['full_name'], repository_id=self.fixture.repo['id'],
            pull_request=42, head=self.pr['head']['sha'], base=self.pr['base']['sha'], branch=self.pr['head']['ref'],
            target=self.pr['base']['ref'], state=self.pr['state'], merged=self.pr['merged'])
        if any(actual[key] != value for key, value in expected.items()):
            return 1, b'private binding conflict'
        return self.transport_run(argv, **bounds)

    def listing(self, rows):
        headers = b'HTTP/2.0 200 OK\r\n'
        if self.next_page:
            headers += b'Link: <https://github.example/next>; rel="next"\r\n'
        return 0, headers + b'\r\n' + json.dumps(rows).encode()

    def transport_run(self, argv, **bounds):
        self.calls.append((argv, bounds))
        if argv[1:3] == ['pr', 'ready']:
            if '--undo' in argv and self.failed_undo:
                return 1, b'private detail'
            self.pr['draft'] = '--undo' in argv
            return 0, b''
        endpoint = argv[-1]
        path = endpoint.split('?', 1)[0]
        method = argv[argv.index('--method') + 1] if '--method' in argv else 'GET'
        payload = json.loads(bounds['input']) if 'input' in bounds else None
        if method != 'GET' and self.metadata_race:
            self.pr.update(title='New human title', body='New human body')
            self.metadata_race = False
        if '/issues/42/labels' in path:
            if method == 'GET':
                return self.listing([dict(name=name) for name in self.labels])
            if method == 'DELETE':
                self.labels.discard(unquote(path.rsplit('/', 1)[1]))
                return 0, b''
            self.labels.update(payload['labels'])
            return 0, json.dumps([dict(name=name) for name in self.labels]).encode()
        if '/issues/42/comments' in path:
            if method == 'GET':
                return self.listing(self.comments)
            comment = dict(id=len(self.comments) + 1, body=payload['body'])
            self.comments.append(comment)
            if self.lost_comment:
                self.lost_comment = False
                raise subprocess.TimeoutExpired(argv[0], 10)
            return 0, json.dumps(comment).encode()
        if '/labels/' in path:
            name = unquote(path.rsplit('/', 1)[1])
            return (0, json.dumps(dict(name=name)).encode()) if name in self.known_labels else (1, b'not found')
        if path.endswith('/labels'):
            self.known_labels.add(payload['name'])
            return 0, json.dumps(payload).encode()
        if method == 'POST' and path.endswith('/pulls'):
            self.creates += 1
            self.exists = True
            self.pr.update({key: payload[key] for key in ('title', 'body', 'draft')})
            if self.lost_create:
                self.lost_create = False
                raise subprocess.TimeoutExpired(argv[0], 10)
        elif method == 'PUT':
            if self.merge_race:
                self.pr['head']['sha'] = 'e' * 40
            if payload['sha'] != self.pr['head']['sha']:
                return 1, b'private expected-head failure'
            self.pr.update(state='closed', draft=False, merged=True,
                merged_at='2026-11-02T12:00:00Z', merge_commit_sha='c' * 40)
        if '/pulls?' in endpoint:
            return self.listing([self.pr] if self.exists else [])
        if '/git/ref/' in path:
            return 0, json.dumps(dict(ref='refs/heads/codex/feature', object=dict(sha='a' * 40))).encode()
        value = self.pr if '/pulls' in path else self.fixture.repo
        return 0, json.dumps(value).encode()

    def operation(self, **updates):
        return dict(publication=deepcopy(self.fixture.publication), head='a' * 40, marker='d' * 64,
            operation_id='a' * 64, repository_id=123, title='Feature', body='Checkpoint\nAccepted progress') | updates

    def test_checkpoint_preserves_human_title_body_and_prior_comments(self):
        self.adapter.execute('checkpoint', self.operation())
        self.pr['body'] += '\nHuman follow-up'
        self.adapter.execute('checkpoint', self.operation(body='Next checkpoint', operation_id='b' * 64))
        self.assertEqual(self.pr['title'], 'Human title')
        self.assertEqual(self.pr['body'], 'Human introduction\nHuman follow-up')
        self.assertIn('Accepted progress', self.comments[0]['body'])
        self.assertIn('Next checkpoint', self.comments[1]['body'])
        self.assertEqual(self.labels, {'[BLOCKED]'})

    def test_checkpoint_never_rewrites_shared_title_or_description(self):
        before = self.pr['title'], self.pr['body']
        self.adapter.execute('checkpoint', self.operation())
        self.assertEqual((self.pr['title'], self.pr['body']), before)
        self.assertFalse(any('PATCH' in argv for argv, _ in self.calls))

    def test_lost_create_ack_discovers_one_draft_instead_of_creating_another(self):
        self.exists, self.lost_create = False, True
        operation = self.operation(publication=self.fixture.publication | dict(pull_request=None))
        with self.assertRaises(ValueError):
            self.adapter.execute('checkpoint', operation)
        result = self.adapter.execute('checkpoint', operation)
        self.assertEqual(self.creates, 1)
        self.assertEqual(result['pull_request'], 42)
        self.assertTrue(result['draft'])
        self.assertEqual(self.pr['title'], 'Feature')
        self.assertEqual(self.labels, {'[BLOCKED]'})

    def test_lost_comment_ack_and_replay_do_not_duplicate_progress(self):
        self.lost_comment = True
        with self.assertRaises(ValueError):
            self.adapter.execute('checkpoint', self.operation())
        self.adapter.execute('checkpoint', self.operation())
        self.assertEqual(len(self.comments), 1)
        with self.assertRaises(ValueError):
            self.adapter.execute('checkpoint', self.operation(body='Changed reused operation'))
        self.assertEqual(len(self.comments), 1)

    def test_incomplete_discovery_cannot_create_or_claim_absence(self):
        self.exists, self.next_page = False, True
        with self.assertRaises(ValueError):
            self.adapter.execute('checkpoint', self.operation(publication=self.fixture.publication | dict(pull_request=None)))
        self.assertEqual(self.creates, 0)

    def test_ready_and_reopen_preserve_the_human_title(self):
        ready = self.adapter.execute('ready', self.operation())
        self.assertFalse(ready['draft'])
        self.assertEqual(self.pr['title'], 'Human title')
        self.assertEqual(self.labels, {'[READY]'})
        reopened = self.adapter.execute('reopen', self.operation(operation_id='b' * 64))
        self.assertTrue(reopened['draft'])
        self.assertEqual(self.pr['title'], 'Human title')
        self.assertEqual(self.labels, {'[BLOCKED]'})

    def test_failed_ready_withdrawal_does_not_report_success(self):
        self.pr['draft'] = False
        self.labels.add('[READY]')
        self.failed_undo = True
        with self.assertRaises(ValueError):
            self.adapter.execute('reopen', self.operation())
        self.assertFalse(self.pr['draft'])
        self.assertEqual(self.pr['title'], 'Human title')
        self.assertNotIn('[READY]', self.labels)

    def test_direct_merge_requires_a_separate_target_enforcement_source(self):
        metadata = GitHubDelivery(executable=sys.executable, host='github.com', run=self.transport_run, mutate=self.transport_run)
        self.pr['draft'] = False
        with self.assertRaises(ValueError):
            metadata.execute('merge', self.operation(method='squash'))
        self.assertFalse(any('PUT' in argv for argv, _ in self.calls))
        result = self.adapter.execute('merge', self.operation(method='squash'))
        self.assertTrue(result['merged'])
        mutation = next((argv, bounds) for argv, bounds in self.calls if 'PUT' in argv)
        self.assertEqual(json.loads(mutation[1]['input']), dict(sha='a' * 40, merge_method='squash'))
        flattened = [value for argv, _ in self.calls for value in argv]
        for forbidden in ('--admin', '--auto', '--delete-branch', 'merge-async'):
            self.assertNotIn(forbidden, flattened)
        self.calls.clear()
        self.adapter.execute('merge', self.operation(method='squash'))
        self.assertFalse(any('--method' in argv for argv, _ in self.calls))

    def test_changed_head_is_rejected_by_the_conditional_merge_request(self):
        self.pr['draft'], self.merge_race = False, True
        with self.assertRaises(ValueError) as error:
            self.adapter.execute('merge', self.operation(method='squash'))
        self.assertFalse(self.pr['merged'])
        self.assertNotIn('private expected-head failure', str(error.exception))

    def test_unowned_existing_branch_pr_is_not_replaced(self):
        with self.assertRaises(ValueError):
            self.adapter.execute('checkpoint', self.operation(publication=self.fixture.publication | dict(pull_request=None)))
        self.assertEqual(self.creates, 0)

    def test_plain_read_executor_cannot_enable_a_provider_mutation(self):
        plain = GitHubDelivery(executable=sys.executable, host='github.com', run=self.transport_run)
        with self.assertRaises(ValueError):
            plain.execute('checkpoint', self.operation())
        self.assertFalse(any('--method' in argv for argv, _ in self.calls))

    def test_concurrent_human_text_is_not_overwritten(self):
        self.metadata_race = True
        self.adapter.execute('checkpoint', self.operation())
        self.assertEqual(self.pr['title'], 'New human title')
        self.assertEqual(self.pr['body'], 'New human body')

    def test_retarget_cannot_merge_into_an_unapproved_branch(self):
        self.pr['draft'], self.target_race = False, True
        with self.assertRaises(ValueError):
            self.adapter.execute('merge', self.operation(method='squash'))
        self.assertFalse(self.pr['merged'])

    def test_reused_operation_conflicts_before_ready_or_label_changes(self):
        self.adapter.execute('checkpoint', self.operation())
        with self.assertRaises(ValueError):
            self.adapter.execute('ready', self.operation(body='Changed reused operation'))
        self.assertTrue(self.pr['draft'])
        self.assertEqual(self.labels, {'[BLOCKED]'})
        self.assertEqual(len(self.comments), 1)


if __name__ == '__main__':
    unittest.main()
