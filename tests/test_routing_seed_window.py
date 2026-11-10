"""S6: the one-shot operator op `seed_window` (C.3 design 7.1 decision 6; contract in clients/opencode.md).

The first managed launch on a subscription route has no stored window (`window_unobserved`). The operator seeds
one; the seed is fresh for the usual 900 s, `status` shows it as `source: operator`, and the first real host
observation replaces it, never the other way round. No model, network or host call is made.
"""
import io
import json
import sqlite3
import time
import unittest
from unittest import mock

from runtime import routing_observations as collector, routing_settings
from scripts import routing as command
from tests.test_routing_launch import IDENTITY, job
from tests.test_routing_observations import SCOPE, Stores, gateway_route, gateway_window, sub_route, sub_window

SECRET = 'sk-never-in-the-audit'


def seed(event_id='seed-1', **change):
    value = dict(v=1, op='seed_window', pool='sub', window='5h', used=100, limit=1000, reset_at=int(time.time()) + 3600,
                 length=18000, event_id=event_id)
    value.update(change)
    return value


class Local(collector.LocalSource):
    """The production source, reading the real private window store, with a test manifest."""
    ROUTES = (sub_route(), gateway_route())

    def manifest(self):
        return [dict(route) for route in self.ROUTES]


class SeedBase(Stores):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(collector, 'default_source', lambda settings_path=None: Local(settings_path))
        patch.start()
        self.addCleanup(patch.stop)
        self.service.close()
        self.service = command.Service(self.settings, self.ledger_path, one_shot=True)
        self.addCleanup(self.service.close)

    def stored(self):
        path = collector.window_path(self.settings)
        return json.loads(path.read_text())['pools'] if path.exists() else None

    def events(self):
        with sqlite3.connect(self.ledger_path) as db:
            return db.execute("SELECT event_id, body FROM events").fetchall()

    def collect(self):
        return collector.collect(str(self.root), dict(scope=SCOPE), settings_path=self.settings,
                                 ledger_path=self.ledger_path)


class ContractTests(SeedBase):
    def test_seed_window_is_an_operator_op_with_exactly_its_fields(self):
        self.assertIn('seed_window', command.OPERATOR_OPS)
        self.assertEqual(command.FIELDS['seed_window'],
                         ({'pool', 'window', 'used', 'limit', 'reset_at', 'length', 'event_id'}, set()))

    def test_serve_and_in_process_services_never_seed(self):
        for service in (command.Service(self.settings, self.ledger_path, serving=True, one_shot=True),
                        command.Service(self.settings, self.ledger_path)):
            self.addCleanup(service.close)
            response = service.handle(json.dumps(seed()).encode())
            self.assertEqual(response, dict(v=1, state='blocked', reason='operator_op_not_served'))
        stdout = io.BytesIO()
        command.serve(command.Service(self.settings, self.ledger_path, serving=True),
                      io.BytesIO(json.dumps(seed()).encode() + b'\n'), stdout)
        self.assertEqual(json.loads(stdout.getvalue())['reason'], 'operator_op_not_served')
        self.assertIsNone(self.stored())
        self.assertFalse(self.ledger_path.exists())

    def test_a_record_event_cannot_reach_seed_window(self):
        response = self.call(dict(v=1, op='record', event=dict(seed(), kind='seed_window')))
        self.assertEqual(response['state'], 'blocked')
        self.assertIsNone(self.stored())


class BoundsTests(SeedBase):
    def test_every_out_of_bounds_request_is_invalid_and_writes_nothing(self):
        at = int(time.time())
        cases = dict(bool_used=dict(used=True), float_used=dict(used=1.0), negative_used=dict(used=-1),
                     bool_limit=dict(limit=True), float_limit=dict(limit=1000.0), zero_limit=dict(limit=0, used=0),
                     over_limit=dict(used=1001), bool_length=dict(length=True), zero_length=dict(length=0),
                     float_length=dict(length=18000.5), string_used=dict(used='1'), huge=dict(limit=2**60),
                     past_reset=dict(reset_at=at - 1), reset_now=dict(reset_at=at),
                     reset_beyond_length=dict(reset_at=at + 18000 + 120), float_reset=dict(reset_at=at + 10.5),
                     bool_reset=dict(reset_at=True), null_reset=dict(reset_at=None), bad_pool=dict(pool='a/b'),
                     empty_window=dict(window=''), bad_event=dict(event_id=''), int_event=dict(event_id=7))
        for name, change in cases.items():
            with self.subTest(name):
                response = self.call(seed(**change))
                self.assertEqual(response, dict(v=1, state='blocked', reason='invalid_request'))
        self.assertEqual(self.call(dict(seed(), extra=1))['reason'], 'invalid_request')
        missing = seed()
        del missing['length']
        self.assertEqual(self.call(missing)['reason'], 'invalid_request')
        self.assertIsNone(self.stored())

    def test_the_boundary_values_are_accepted(self):
        at = int(time.time())
        response = self.call(seed(used=1000, limit=1000, length=7200, reset_at=at + 7200 - 5))
        self.assertEqual(response['state'], 'recorded', response)
        response = self.call(seed('seed-2', used=0, limit=1, length=1, reset_at=at + 1, window='1s'))
        self.assertEqual(response['reason'], 'invalid_request')  # `1s` is no subscription route's window

    def test_only_a_subscription_window_named_by_the_manifest_is_seeded(self):
        for change in (dict(pool='gw', window='credit'), dict(pool='unknown'), dict(window='weekly')):
            with self.subTest(change):
                self.assertEqual(self.call(seed(**change))['reason'], 'invalid_request')
        mixed = (sub_route(), sub_route('api', billing_class='api_prepaid', pool='sub', demand={'5h': 1}))
        with mock.patch.object(Local, 'ROUTES', mixed):  # a pool any dollar route draws on is a dollar pool
            self.assertEqual(self.call(seed())['reason'], 'invalid_request')
        unqualified = (sub_route(status='unverified'),)
        with mock.patch.object(Local, 'ROUTES', unqualified):
            self.assertEqual(self.call(seed())['reason'], 'invalid_request')
        self.assertIsNone(self.stored())

    def test_an_organization_window_is_never_seeded(self):
        collector.store_window(self.settings, 'sub', dict(gateway_window(self.now - 2000), id='5h'))
        self.assertEqual(self.call(seed())['reason'], 'invalid_request')
        self.assertIn('organization', self.stored()['sub']['5h'])


class StoreTests(SeedBase):
    def test_a_seed_is_stored_in_the_window_unit_with_source_operator_and_the_collector_accepts_it(self):
        reset = int(time.time()) + 3600
        response = self.call(seed(reset_at=reset))
        self.assertEqual(response, dict(v=1, state='recorded', duplicate=False, outcome='window_seeded', pool='sub',
                                        window='5h', used=100, limit=1000, reset_at=reset, length=18000,
                                        observed_at=response['observed_at'], event_id='seed-1'))
        self.assertLessEqual(abs(response['observed_at'] - time.time()), 2)
        self.assertEqual(self.stored()['sub']['5h'], dict(used=100, limit=1000, reset_at=reset, length=18000,
                                                          rolling=False, unit=collector.WINDOW_UNIT,
                                                          source='operator', observed_at=response['observed_at']))
        result = self.collect()
        self.assertEqual([route['route'] for route in result['observations']['routes']], ['sub'])
        window, = result['observations']['pools']['sub']['windows']
        self.assertEqual((window['fresh'], window['used'], window['unit']), (True, 100, collector.WINDOW_UNIT))
        self.assertNotIn('source', window)  # admission sees a window, not who wrote it

    def test_a_seed_is_fresh_for_900_seconds(self):
        observed = self.call(seed())['observed_at']
        for offset, fresh in ((collector.FRESH_SECONDS, True), (collector.FRESH_SECONDS + 1, False)):
            with self.subTest(offset), mock.patch.object(collector.time, 'time', return_value=observed + offset):
                window, = self.collect()['observations']['pools']['sub']['windows']
                self.assertIs(window['fresh'], fresh)

    def test_a_fresh_host_observation_refuses_the_seed_and_a_stale_one_does_not(self):
        collector.store_window(self.settings, 'sub', sub_window(self.now - 10))
        before = self.stored()
        self.assertEqual(self.call(seed()), dict(v=1, state='blocked', reason='fresh_observation_exists'))
        self.assertEqual(self.stored(), before)
        self.assertEqual(self.events(), [])  # refusals are not remembered
        ahead = self.root / 'ahead'
        settings = ahead / 'routing' / 'settings.json'
        collector.store_window(settings, 'sub', sub_window(self.now + 30))  # dated ahead, within the clock margin
        service = command.Service(settings, ahead / 'usage' / 'l.sqlite3', one_shot=True)
        self.addCleanup(service.close)
        self.assertEqual(service.handle(json.dumps(seed()).encode())['reason'], 'fresh_observation_exists')
        stale = self.root / 'stale'
        settings = stale / 'routing' / 'settings.json'
        collector.store_window(settings, 'sub', sub_window(self.now - collector.FRESH_SECONDS - 5))
        service = command.Service(settings, stale / 'usage' / 'l.sqlite3', one_shot=True)
        self.addCleanup(service.close)
        self.assertEqual(service.handle(json.dumps(seed()).encode())['state'], 'recorded')

    def test_a_seed_may_replace_an_earlier_seed(self):
        self.assertEqual(self.call(seed(used=100))['state'], 'recorded')
        self.assertEqual(self.call(seed('seed-2', used=300))['state'], 'recorded')
        self.assertEqual(self.stored()['sub']['5h']['used'], 300)

    def test_the_first_host_observation_replaces_the_seed_never_the_other_way_round(self):
        observed = self.call(seed())['observed_at']
        host = sub_window(observed - 5, used=420)  # even a reading taken just before the seed wins
        collector.store_window(self.settings, 'sub', host)
        window = self.stored()['sub']['5h']
        self.assertEqual((window['used'], window.get('source')), (420, None))
        self.assertEqual(self.call(seed('seed-2'))['reason'], 'fresh_observation_exists')
        self.assertEqual(self.stored()['sub']['5h']['used'], 420)

    def test_the_host_path_can_never_store_an_operator_source(self):
        for source in ('operator', 'host'):
            with self.subTest(source), self.assertRaises(ValueError):
                collector.store_window(self.settings, 'sub', sub_window(self.now, source=source))
        self.assertIsNone(self.stored())


class IdempotencyTests(SeedBase):
    def test_the_same_payload_is_a_duplicate_and_another_is_event_conflict(self):
        request = seed()
        first = self.call(request)
        stored = self.stored()
        with mock.patch.object(command.Service, 'clock', staticmethod(lambda: first['observed_at'] + 5)):
            again = self.call(request)
        self.assertEqual(again, dict(first, duplicate=True))
        self.assertEqual(self.stored(), stored)  # the replay wrote nothing
        self.assertEqual(self.call(seed(used=101)), dict(v=1, state='blocked', reason='event_conflict'))
        self.assertEqual(self.stored(), stored)

    def test_the_replay_survives_the_reset_passing(self):
        request = seed(reset_at=int(time.time()) + 10, length=60)
        first = self.call(request)
        with mock.patch.object(command.Service, 'clock', staticmethod(lambda: first['observed_at'] + 100)):
            self.assertEqual(self.call(request), dict(first, duplicate=True))

    def test_the_audit_row_holds_labels_and_integers_only(self):
        self.call(seed(event_id='audit-1'))
        (event_id, raw), = self.events()
        body = json.loads(raw)
        self.assertEqual(event_id, 'audit-1')
        self.assertEqual(body['kind'], 'seed_window')
        self.assertEqual(set(body), {'attempt_id', 'kind', 'recorded_at', 'outcome'})
        self.assertTrue(body['attempt_id'].startswith('seed:'))
        self.assertEqual(set(body['outcome']), {'state', 'duplicate', 'outcome', 'pool', 'window', 'used', 'limit',
                                                'reset_at', 'length', 'observed_at'})
        self.assertNotIn(SECRET, raw)


class StatusTests(SeedBase):
    def test_status_shows_each_stored_window_and_its_source(self):
        self.assertEqual(self.call(dict(v=1, op='status'))['windows'], dict(state='ok', windows=[], more=0))
        observed = self.call(seed())['observed_at']
        windows = self.call(dict(v=1, op='status'))['windows']
        self.assertEqual(windows, dict(state='ok', more=0, windows=[dict(pool='sub', window='5h', source='operator',
                                                                         observed_at=observed, fresh=True)]))
        collector.store_window(self.settings, 'sub', sub_window(observed + 1))
        listed, = self.call(dict(v=1, op='status'))['windows']['windows']
        self.assertEqual((listed['source'], listed['observed_at']), ('host', observed + 1))

    def test_an_unreadable_window_store_never_breaks_status(self):
        path = collector.window_path(self.settings)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text('[]')
        path.chmod(0o600)
        response = self.call(dict(v=1, op='status'))
        self.assertEqual((response['state'], response['windows']), ('ok', dict(state='unavailable', reason='observation_store_invalid')))
        self.assertEqual(self.call(seed())['reason'], 'observation_store_invalid')


class EndToEndTests(SeedBase):
    """A managed project whose manifest route has no stored window: seed it, `prepare` is admitted; a later host
    observation replaces the seed; `status` shows source operator, then the host."""

    def test_seed_admits_then_the_host_observation_takes_over(self):
        checkout = self.root / 'checkout'
        checkout.mkdir()
        routing_settings.update(self.settings, dict(managed_projects=[str(checkout)]), 0)
        pinned = command.Service(self.settings, self.ledger_path, project=checkout)
        self.addCleanup(pinned.close)

        def prepare(attempt):
            request = dict(v=1, op='prepare', job=job(), assignment=dict(
                attempt_id=attempt, task='task-' + attempt, scope=SCOPE, choice=dict(IDENTITY, funding='subscription')))
            return pinned.handle(json.dumps(request).encode())

        def status():
            stdout = io.BytesIO()
            self.assertEqual(command.main(['status', '--settings', str(self.settings), '--ledger',
                                           str(self.ledger_path)], io.BytesIO(b'{"v":1}'), stdout), 0)
            return {(item['pool'], item['window']): item['source']
                    for item in json.loads(stdout.getvalue())['windows']['windows']}

        unobserved = prepare('e1')
        self.assertEqual((unobserved['state'], unobserved['reason']), ('pending', 'window_unobserved'), unobserved)
        self.assertEqual(status(), {})
        self.assertEqual(pinned.handle(json.dumps(seed()).encode())['reason'], 'operator_op_not_served')

        stdout = io.BytesIO()
        code = command.main(['seed_window', '--settings', str(self.settings), '--ledger', str(self.ledger_path)],
                            io.BytesIO(json.dumps(seed()).encode()), stdout)
        seeded = json.loads(stdout.getvalue())
        self.assertEqual((code, seeded['state'], seeded['outcome']), (0, 'recorded', 'window_seeded'), seeded)
        self.assertEqual(status(), {('sub', '5h'): 'operator'})

        admitted = prepare('e2')
        self.assertEqual(admitted['state'], 'admitted', admitted)
        self.assertEqual((admitted['admission']['route'], admitted['admission']['pool']), ('sub', 'sub'))

        collector.store_window(self.settings, 'sub', sub_window(int(time.time()), used=250))
        self.assertEqual(status(), {('sub', '5h'): 'host'})
        stdout = io.BytesIO()
        command.main(['seed_window', '--settings', str(self.settings), '--ledger', str(self.ledger_path)],
                     io.BytesIO(json.dumps(seed('seed-2')).encode()), stdout)
        self.assertEqual(json.loads(stdout.getvalue())['reason'], 'fresh_observation_exists')
        self.assertEqual(status(), {('sub', '5h'): 'host'})
        self.assertEqual(prepare('e3')['state'], 'admitted')


if __name__ == '__main__':
    unittest.main()
