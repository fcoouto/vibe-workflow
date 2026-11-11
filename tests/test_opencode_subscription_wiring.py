"""F4.5: the model-free OpenCode Go path, its per-model demand gate and its stale-and-needed usage refresh.

Real producers stay absent: no Go window semantics/observation-time binding, no per-model demand mapping and no
billing fact are qualified, so production never reads Go usage and every Go route stays out. Positive behavior here
comes only from labelled code-level doubles. Nothing reads an account, client, network or file outside the stores.
"""
import io
import json
import unittest
from unittest import mock

from runtime import opencode_subscription as subscription, routing, routing_launch, routing_observations as collector, \
    routing_paths, routing_settings, subscription_billing
from scripts import routing as command
from tests.test_routing_observations import SCOPE, Stores

GO = 'opencode-go-subscription'
def go_rule():
    return next(item for item in routing_paths.load_rules()['paths'] if item['path'] == GO)


def usage(percent=(10, 20, 30), limited=(False, False, False), workspace='wrk_invented', at=None):
    resets = (at + 3600) * 1000
    return dict(location=dict(directory='/invented'), data=dict(
        workspace=dict(id=workspace, usageURL='https://example.invalid/usage'), budgets=[],
        go={name: dict(percent=value, limited=flag, resetsAt=resets)
            for name, value, flag in zip(('rolling', 'weekly', 'monthly'), percent, limited)}))


def semantics_double(parsed, *, requested_at, completed_at):
    """Labelled test double for the unqualified Go meter binding: invented lengths, rolling flags and an observation
    time equal to the request start. Never a production value."""
    lengths = dict(rolling=5 * 3600, weekly=7 * 86400, monthly=30 * 86400)
    return [dict(id=name, limit=meter['limit'], used=meter['used'], length=lengths[name],
                 reset_at=int(meter['reset_at']),
                 rolling=name == 'rolling', unit=collector.WINDOW_UNIT, observed_at=requested_at)
            for name, meter in parsed['meters'].items()]


class GoPathTests(unittest.TestCase):
    def test_the_go_path_is_model_free_with_its_own_three_constraint_pool(self):
        rule = go_rule()
        self.assertEqual((rule['provider'], rule['harness']),
                         ('opencode-go', routing_launch.shim_harness('opencode-go')))
        self.assertEqual(rule['billing_class'], 'included_subscription')
        self.assertEqual(rule['windows'], ['rolling', 'weekly', 'monthly'])  # constraints, not invented durations
        self.assertEqual(rule.get('demand_scope'), 'per_model')
        self.assertFalse({'model', 'effort', 'demand', 'endpoint'} & set(rule))
        others = [item for item in routing_paths.load_rules()['paths'] if item['path'] != GO]
        self.assertNotIn(rule['pool'], {item['pool'] for item in others})

    def test_demand_scope_is_validated(self):
        rules = routing_paths.load_rules()
        for value in ('model', 'path', '', None, True):
            with self.subTest(demand_scope=value):
                broken = dict(rules, paths=[dict(item, demand_scope=value) if item['path'] == GO else item
                                            for item in rules['paths']])
                path = self.enterContext(_written(broken))
                with self.assertRaises(ValueError):
                    routing_paths.load_rules(path)


class _written:
    def __init__(self, value):
        import tempfile
        self.folder = tempfile.TemporaryDirectory()
        self.value = value

    def __enter__(self):
        from pathlib import Path
        path = Path(self.folder.name) / 'rules.json'
        path.write_text(json.dumps(self.value))
        return path

    def __exit__(self, *args):
        self.folder.cleanup()


class GoFlowBase(Stores):
    """A managed checkout with the Go path qualified on temporary stores, ranked by a table with two Go aliases."""

    def setUp(self):
        super().setUp()
        self.checkout = self.root / 'checkout'
        self.checkout.mkdir()
        routing_settings.update(self.settings, dict(managed_projects=[str(self.checkout)]), 0)
        self.service.close()
        self.service = command.Service(self.settings, self.ledger_path, project=self.checkout)
        self.addCleanup(self.service.close)
        self.account = subscription.account_label('wrk_invented')
        routing_paths.qualify(self.settings, GO, demand=dict(rolling=5, weekly=5, monthly=5), evidence='synthetic-1',
                              account=self.account, now=self.now)
        self.job = routing_launch.unplanned_job('software-engineer', SCOPE, 'Do the fixture task')
        profile = routing.ensure_profile(self.job)
        row = '|'.join((profile['work_kind'], profile['complexity_band'], profile['risk_band']))
        strategies = [dict(strategy_id=sid, model=model, effort='high', harness=go_rule()['harness'],
                           provider='opencode-go', evidence_refs=['g'])
                      for sid, model in (('s-go1', 'invented-go-one'), ('s-go2', 'invented-go-two'))]
        target = self.settings.parent / 'strategy-table.json'
        target.write_text(json.dumps(dict(schema=2, version='tv-go', rows={row: strategies})))
        target.chmod(0o600)

    def doubles(self, billing=True, demand=True, semantics=True):
        """Labelled code-level doubles for the producers that are not qualified in production."""
        if billing:
            self.enterContext(mock.patch.object(collector.LocalSource, 'billing', lambda source, route, now:
                                                subscription_billing.double('go-wiring')(route, now)))
        if demand:
            self.enterContext(mock.patch.object(collector.LocalSource, 'model_demand', lambda source, route: True))
        if semantics:
            self.enterContext(mock.patch.object(subscription, 'window_semantics', lambda: semantics_double))

    def request(self):
        assignment = dict(attempt_id='a1', task='task-a1', scope=routing_launch.assignment_scope(self.checkout))
        return routing_launch.prepare_request(assignment, self.job, harnesses=[go_rule()['harness']])


class DemandGateTests(GoFlowBase):
    def preview(self):
        return self.service.preview({key: value for key, value in self.request().items() if key not in ('v', 'op')})

    def test_a_model_scoped_path_needs_a_verified_model_demand_mapping(self):
        self.doubles(demand=False)
        found = self.preview()
        self.assertEqual(found['routes'], [])
        self.assertEqual({item['reason'] for item in found['excluded']}, {'demand_unverified'})
        self.assertIsNone(collector.LocalSource(self.settings).model_demand(dict(route=GO + ':s-go1')))

    def test_a_verified_mapping_keeps_both_aliases_on_one_pool(self):
        self.doubles()
        found = self.preview()
        self.assertEqual(found['routes'], [GO + ':s-go1', GO + ':s-go2'])
        specs = collector.LocalSource(self.settings).manifest(json.loads(
            (self.settings.parent / 'strategy-table.json').read_text()))
        self.assertEqual({spec['pool'] for spec in specs}, {go_rule()['pool']})  # aliases share one allowance

    def test_the_real_producers_refuse_billing_first(self):
        found = self.preview()
        self.assertEqual({item['reason'] for item in found['excluded']}, {'billing_unverified'})


class RefreshTests(GoFlowBase):
    def refresh(self, read):
        return subscription.refresh(self.service, read, self.request(), now=self.now)

    def stored(self):
        return collector.LocalSource(self.settings).windows(go_rule()['pool'], self.now)

    def state_file(self):
        return collector.window_path(self.settings).parent / subscription.STATE_FILE

    def test_a_held_reader_lock_answers_busy_without_reading(self):
        from runtime import codex_rate_limits
        self.doubles()
        read = mock.Mock(side_effect=AssertionError('read while another reader holds the pool'))
        with codex_rate_limits._pool_lock(self.settings, subscription.LOCK_PREFIX + go_rule()['pool']) as held:
            self.assertTrue(held)
            self.assertEqual(self.refresh(read), dict(state='unavailable', reason='reader_busy'))  # no waiting

    def test_freshness_is_rechecked_once_the_lock_is_held(self):
        self.doubles()
        from runtime import codex_rate_limits
        real = codex_rate_limits._pool_lock

        def lock(settings_path, pool, wait=0.0):  # another reader stores a fresh reading just before we hold it
            for window in semantics_double(subscription.normalize_limits(usage(at=self.now), observed_at=self.now),
                                           requested_at=int(__import__('time').time()), completed_at=0):
                collector.store_window(self.settings, go_rule()['pool'], window)
            return real(settings_path, pool, wait)
        self.enterContext(mock.patch.object(codex_rate_limits, '_pool_lock', lock))
        read = mock.Mock(side_effect=AssertionError('read although the pool turned fresh'))
        self.assertEqual(self.refresh(read), dict(state='fresh'))

    def test_a_failed_read_backs_off_and_the_backoff_expires(self):
        self.doubles()
        failing = mock.Mock(return_value=(503, None))
        self.assertEqual(self.refresh(failing), dict(state='unavailable', reason='usage_unreadable'))
        self.assertEqual(failing.call_count, 1)
        self.assertEqual(self.refresh(failing), dict(state='backoff', reason='usage_unreadable'))  # no new read
        self.assertEqual(failing.call_count, 1)
        recorded = json.loads(self.state_file().read_text())
        self.assertEqual(set(recorded['pools'][go_rule()['pool']]), {'at', 'reason'})  # no payload is kept
        later = __import__('time').time() + subscription.FAILURE_BACKOFF + 1
        good = mock.Mock(return_value=(200, usage(at=int(later))))
        with mock.patch('time.time', lambda: later):
            self.assertEqual(subscription.refresh(self.service, good, self.request(), now=int(later))['state'], 'stored')
        self.assertEqual(good.call_count, 1)
        self.assertIsNone(json.loads(self.state_file().read_text())['pools'][go_rule()['pool']])  # cleared

    def test_the_backoff_equals_the_codex_readers(self):
        from runtime import codex_rate_limits
        self.assertEqual(subscription.FAILURE_BACKOFF, codex_rate_limits.FAILURE_BACKOFF)

    def test_an_unusable_reading_backs_off_too(self):
        self.doubles()
        other = mock.Mock(return_value=(200, usage(at=self.now, workspace='wrk_other')))
        self.assertEqual(self.refresh(other), dict(state='unavailable', reason='account_mismatch'))
        self.assertEqual(self.refresh(other), dict(state='backoff', reason='account_mismatch'))
        self.assertEqual(other.call_count, 1)

    def test_production_has_no_semantics_binding_and_never_reads(self):
        self.doubles(semantics=False)
        read = mock.Mock(side_effect=AssertionError('usage read without a qualified binding'))
        self.assertIsNone(subscription.window_semantics())
        self.assertEqual(self.refresh(read), dict(state='skipped', reason='window_semantics_unqualified'))
        self.assertEqual(self.stored(), [])
        self.assertFalse(self.state_file().exists())  # no reader state, lock or backoff record is written
        self.assertEqual(sorted(path.name for path in self.state_file().parent.glob('*opencode-go*')), [])

    def test_a_kept_stale_go_route_is_read_once_and_stored_with_the_bindings_semantics(self):
        self.doubles()
        read = mock.Mock(return_value=(200, usage(at=self.now)))
        self.assertEqual(self.refresh(read)['state'], 'stored')
        self.assertEqual(read.call_count, 1)
        self.assertEqual({window['id']: window['used'] for window in self.stored()},
                         dict(rolling=100, weekly=200, monthly=300))
        read.reset_mock()
        self.assertEqual(self.refresh(read), dict(state='fresh'))  # fresh: no second read
        self.assertEqual(read.call_count, 0)

    def test_an_unusable_reading_stores_nothing(self):
        self.doubles()
        cases = [((500, None), 'usage_unreadable'), ((200, dict(location={}, data=None)), 'not_signed_in'),
                 ((200, usage(at=self.now, workspace='wrk_other')), 'account_mismatch')]
        for answer, reason in cases:
            with self.subTest(reason=reason):
                self.state_file().unlink(missing_ok=True)  # each case starts outside the failed-read backoff
                self.assertEqual(self.refresh(mock.Mock(return_value=answer)), dict(state='unavailable', reason=reason))
                self.assertEqual(self.stored(), [])
        self.state_file().unlink(missing_ok=True)
        self.assertEqual(self.refresh(mock.Mock(side_effect=OSError('lost'))),
                         dict(state='unavailable', reason='usage_unreadable'))

    def test_a_binding_that_does_not_cover_every_constraint_stores_nothing(self):
        self.doubles(semantics=False)
        partial = lambda parsed, **times: semantics_double(parsed, **times)[:2]  # noqa: E731
        self.enterContext(mock.patch.object(subscription, 'window_semantics', lambda: partial))
        self.assertEqual(self.refresh(mock.Mock(return_value=(200, usage(at=self.now)))),
                         dict(state='unavailable', reason='window_semantics_unusable'))
        self.assertEqual(self.stored(), [])

    def test_no_kept_go_route_means_no_read(self):
        self.doubles(demand=False)  # statically ineligible: never a reason to read
        read = mock.Mock(side_effect=AssertionError('read for a route the shortlist left out'))
        self.assertEqual(self.refresh(read), dict(state='skipped', reason='no_kept_go_route'))

    def test_go_windows_cannot_be_seeded(self):
        """No host length exists for the Go constraints, so the recovery seed refuses them (no guessed duration)."""
        request = dict(v=1, op='seed_window', pool=go_rule()['pool'], window='weekly', used=1, limit=1000,
                       reset_at=self.now + 3600, length=7 * 86400, event_id='seed-go')
        stdout = io.BytesIO()
        command.main(['seed_window', '--settings', str(self.settings), '--ledger', str(self.ledger_path)],
                     io.BytesIO(json.dumps(request).encode()), stdout)
        self.assertEqual(json.loads(stdout.getvalue())['state'], 'blocked')
        self.assertEqual(self.stored(), [])


if __name__ == '__main__':
    unittest.main()
