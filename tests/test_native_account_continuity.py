"""Generation-bound native account continuity (account-continuity design, Task 3A).

Each native producer's state keeps `identity={key, generation}` beside its account record. A generation survives
only proved continuity: a coherent complete or pending state of the same key. Identity, the matching catalog, the
complete account and confirmed quota are published in that order. A legacy, cleared, pending, corrupt or re-created
state never carries a generation forward, and nothing incomplete yields a billing fact or a native binding.

Real disposable producer stores and the existing synthetic native transports only (the synthetic Claude executable
of `tests/test_claude_usage.py`, `tests/codex_app_server_double.py`); no installed client, account or credential is
reachable. Passing these tests proves the handling of those doubles, not a real account's behaviour.
"""
import re
import time
import unittest
from unittest import mock

from runtime import claude_usage, codex_account, routing_observations as collector
from runtime.adapters import native_catalog
from tests import test_claude_usage as claude_tests, test_codex_account as codex_tests

HEX = re.compile('[0-9a-f]{32}')
CLAUDE_ROUTE = dict(route='claude-native-subscription:s1', account='claude-plan', pool='claude-subscription',
                    billing_class='included_subscription')
CODEX_ROUTE = dict(route='codex-chatgpt-subscription:s1', account='chatgpt-plan', pool='chatgpt-subscription',
                   billing_class='included_subscription')


def failing_write(n):
    """Fail the `n`th private-store write (a crash between publication steps); every other write proceeds."""
    real, count = collector._write, [0]

    def write(path, value):
        count[0] += 1
        if count[0] == n:
            raise OSError('injected')
        return real(path, value)
    return mock.patch.object(collector, '_write', write)


class Generations:
    producer = route = None

    def generation(self):
        found = self.producer._state(self.settings).get('identity')
        return found.get('generation') if type(found) is dict else None

    def fact(self):
        return self.producer.billing_fact(self.settings, self.route, int(time.time()))

    def assert_nothing_eligible(self):
        self.assertIsNone(self.fact())
        self.assertIsNone(self.producer.binding(self.settings))

    def assert_bound(self, generation):
        """The fact, the binding and the stored catalog all carry `generation`."""
        self.assertTrue(HEX.fullmatch(generation or ''), generation)
        self.assertTrue(self.fact()['producer'].endswith(':' + generation))
        connection = self.producer.binding(self.settings)['connection']
        self.assertTrue(connection.startswith('account:') and connection.endswith(':' + generation), connection)
        self.assertEqual(native_catalog.stored(self.settings, self.producer.CLIENT)['binding']['connection'],
                         connection)


class Recovery:
    """Review R-1: only a malformed but safe producer-owned state is reinitialized, through acquisition."""

    def count(self):
        return self.spawns() if type(self.spawns()) is int else len(self.spawns())

    def test_a_malformed_private_state_recovers_only_through_acquisition(self):
        self.read()
        seen = {self.generation()}
        for raw in (b'{not json', b'[]', b'{"version": 2}'):
            with self.subTest(raw=raw):
                self.producer._store(self.settings).write_bytes(raw)
                self.assert_nothing_eligible()  # pure reads of it stay ineligible
                self.assertEqual(self.failed_attempt()['state'], 'failed')  # a failed attempt revives nothing
                self.assert_nothing_eligible()
                self.assertEqual(self.read()['state'], 'stored')
                self.assertNotIn(self.generation(), seen)
                self.assert_bound(self.generation())
                seen.add(self.generation())

    def test_an_unsafe_state_file_is_never_rewritten_or_acquired_for(self):
        self.read()
        path, spawns = self.producer._store(self.settings), self.count()
        target = path.parent / 'elsewhere.json'
        target.write_bytes(b'{not json')
        target.chmod(0o600)
        raw = path.read_bytes()
        cases = (('mode', lambda: path.chmod(0o644)), ('symlink', lambda: (path.unlink(), path.symlink_to(target))),
                 ('oversized', lambda: (path.unlink(), path.write_bytes(b' ' * (collector.MAX_FILE + 1)),
                                        path.chmod(0o600))))
        for name, make in cases:
            with self.subTest(name):
                make()
                before = (path.is_symlink(), path.read_bytes() if not path.is_symlink() else None, target.read_bytes())
                self.assertEqual(self.read()['state'], 'failed')
                self.assertEqual((path.is_symlink(), path.read_bytes() if not path.is_symlink() else None,
                                  target.read_bytes()), before)
                self.assertEqual(self.count(), spawns)
                self.assert_nothing_eligible()
                if name == 'mode':
                    self.assertEqual(path.read_bytes(), raw)
                    path.chmod(0o600)


class ClaudeContinuityTest(Recovery, Generations, claude_tests.Reader):
    producer, route = claude_usage, CLAUDE_ROUTE

    def read(self, init=None, use=None):
        self.answer(init or claude_tests.initialize(), use or claude_tests.usage(self.now))
        return self.refresh(force=True)

    def failed_attempt(self):  # a timeout before initialization: transport only
        self.answer(claude_tests.initialize(), claude_tests.usage(self.now), hang='initialize')
        return self.refresh(force=True, timeout=0.5)

    def test_a_partial_exchange_never_restores_a_conflicting_account(self):
        self.read()
        first = self.generation()
        self.answer(claude_tests.initialize(email='other@example.invalid'), claude_tests.usage(self.now),
                    hang='get_usage')  # B's initialization arrives, its usage answer never does
        self.assertEqual(self.refresh(force=True, timeout=0.5)['reason'], 'claude_timeout')
        self.assert_nothing_eligible()
        self.read()
        self.assertNotIn(self.generation(), (None, first))

    def test_same_second_a_b_a_rotates_and_never_reuses_old_rows(self):
        self.enterContext(mock.patch('time.time', return_value=float(self.now)))  # one identical clock
        self.assertEqual(self.read(use=claude_tests.usage(self.now, five=10))['state'], 'stored')
        first = self.generation()
        self.assert_bound(first)
        self.assertEqual(self.read(claude_tests.initialize(email='other@example.invalid'))['state'], 'stored')
        other = self.generation()
        self.assert_bound(other)
        self.assertEqual(self.read(use=claude_tests.usage(self.now, five=90))['state'], 'stored')
        again = self.generation()
        self.assert_bound(again)
        self.assertEqual(len({first, other, again}), 3)
        rows = {row['id']: row['used'] for row in claude_usage.usable_windows(self.settings, self.now)}
        self.assertEqual(rows['5h'], 900)  # the new generation's reading, never the first A's row of the same second
        self.assertEqual(len(self.spawns()), 3)

    def test_aba_with_unconfirmed_usage_claims_no_old_row(self):
        self.enterContext(mock.patch('time.time', return_value=float(self.now)))
        self.read()
        self.read(claude_tests.initialize(email='other@example.invalid'))
        self.assertEqual(self.read(use=claude_tests.usage(self.now, limits=None))['state'], 'failed')
        self.assertIsNotNone(self.fact())  # the account and catalog are current
        self.assertEqual(claude_usage.usable_windows(self.settings, self.now), [])  # earlier A rows stay history

    def test_same_account_refresh_and_restart_keep_the_generation(self):
        self.read()
        first, binding = self.generation(), claude_usage.binding(self.settings)
        self.read()
        self.assertEqual(self.generation(), first)
        self.assertEqual(claude_usage.binding(self.settings), binding)  # state is reread from disk on every call
        self.assert_bound(first)

    def test_explicit_refusal_invalidates_continuity(self):
        self.read()
        first = self.generation()
        self.assertEqual(self.read(claude_tests.initialize(tokenSource='CLAUDE_CODE_OAUTH_TOKEN'))['state'], 'failed')
        self.assertIsNone(self.generation())
        self.assert_nothing_eligible()
        self.read()
        self.assertNotIn(self.generation(), (None, first))

    def test_transient_failures_keep_only_proved_continuity(self):
        self.read()
        first = self.generation()
        self.answer(claude_tests.initialize(), claude_tests.usage(self.now), hang='initialize')
        self.assertEqual(self.refresh(force=True, timeout=0.5)['reason'], 'claude_timeout')
        self.assert_bound(first)  # transport only: nothing was observed, so the proved account is settled back
        self.assertEqual(self.read(dict(claude_tests.initialize(), models=[]))['state'], 'failed')
        self.assertEqual(claude_usage._state(self.settings)['account'], dict(pending=True, generation=first))
        self.assert_nothing_eligible()  # the same account observed without a valid catalog: continuity only
        with mock.patch.object(native_catalog, 'store_snapshot', side_effect=ValueError('native_catalog_invalid')):
            self.assertEqual(self.read()['state'], 'failed')  # identity proved, catalog never published
        self.assertEqual(claude_usage._state(self.settings)['account'], dict(pending=True, generation=first))
        self.assert_nothing_eligible()
        self.read()
        self.assert_bound(first)

    def test_legacy_and_old_writer_states_never_resurrect_a_generation(self):
        self.read()
        first = self.generation()
        key = claude_usage._state(self.settings)['account']['key']
        legacy = dict(observed_at=int(time.time()), client_version='9.9.9', overflow='unknown', usage_at=None)
        for name, account in (('old A success', dict(legacy, key=key)), ('old B success', dict(legacy, key='b' * 32)),
                              ('old clear', None)):
            with self.subTest(name):
                claude_usage._save(self.settings, account=account)  # an old writer keeps the retained identity
                self.assertEqual(claude_usage._state(self.settings)['identity']['generation'], first)
                self.assert_nothing_eligible()
        self.read()
        self.assertNotIn(self.generation(), (None, first))

    def test_lost_corrupt_and_recreated_stores(self):
        self.read()
        first = self.generation()
        claude_usage._store(self.settings).unlink()
        self.assert_nothing_eligible()
        self.read()
        recreated = self.generation()
        self.assertNotIn(recreated, (None, first))
        claude_usage._store(self.settings).write_text('{not json')
        self.assert_nothing_eligible()
        self.assertEqual(self.read()['state'], 'stored')  # review R-1: a malformed private state is reinitialized
        self.assertNotIn(self.generation(), (None, first, recreated))  # unsafe states: see the unsafe-file test

    def test_warmed_publication_faults_never_keep_or_resurrect_the_earlier_account(self):
        # Writes: 1 fence, 2 observed identity, 3 catalog, 4 complete account, 5-6 rows, 7 quota confirmation.
        other = claude_tests.initialize(email='other@example.invalid')
        for step in range(1, 8):
            with self.subTest(step=step):
                self.read()
                first, key, spawns = self.generation(), claude_usage._state(self.settings)['account']['key'], \
                    len(self.spawns())
                with failing_write(step):
                    self.assertEqual(self.read(other)['state'], 'failed')
                self.assertEqual(len(self.spawns()) - spawns, 0 if step == 1 else 1)  # no fence, no exchange
                current = claude_usage.account(self.settings, int(time.time()))
                if step == 1:  # nothing was observed: the earlier account stays current
                    self.assert_bound(first)
                else:  # B was observed: the earlier account is never current again, B only once complete
                    self.assertEqual(current is not None, step >= 5)
                    self.assertTrue(current is None or current['key'] != key)
                self.read()
                self.assertEqual(self.generation() == first, step == 1)

    def test_an_observed_account_with_an_invalid_catalog_breaks_continuity(self):
        self.read()
        first = self.generation()
        refused = dict(claude_tests.initialize(email='other@example.invalid'), models=[])
        self.assertEqual(self.read(refused)['state'], 'failed')
        self.assert_nothing_eligible()  # neither the earlier account nor the observed one without its catalog
        self.read()
        self.assertNotIn(self.generation(), (None, first))

    def test_a_failure_after_each_publication_step_exposes_nothing_early(self):
        # Writes: 1 fence, 2 identity and pending account, 3 catalog, 4 complete account, 5-6 rows, 7 confirmation.
        for step, eligible, usable in ((1, False, False), (2, False, False), (3, False, False), (4, False, False),
                                       (5, True, False), (6, True, False), (7, True, False)):
            with self.subTest(step=step):
                for path in (claude_usage._store(self.settings), native_catalog._path(self.settings),
                             collector.window_path(self.settings)):
                    path.unlink(missing_ok=True)
                with failing_write(step):
                    self.assertEqual(self.read()['state'], 'failed')
                self.assertEqual(self.fact() is not None, eligible)
                self.assertEqual(claude_usage.binding(self.settings) is not None, eligible)
                self.assertEqual(claude_usage.usable(self.settings, int(time.time())), usable)
                if eligible:
                    self.assert_bound(self.generation())


class CodexContinuityTest(Recovery, Generations, codex_tests.Sandbox):
    producer, route = codex_account, CODEX_ROUTE

    def failed_attempt(self):  # the account is observed, its catalog refused
        return self.read(codex_tests.payload(pages=[dict(data=[], nextCursor=None)]))

    def read(self, value=None, scenario='account'):
        return codex_account.refresh(self.settings, force=True, environ=self.environ,
                                     command=self.command(scenario, codex_tests.payload() if value is None else value))

    def other(self):
        return codex_tests.payload(accounts=[codex_tests.identity('other@example.test')] * 2)

    def test_same_second_a_b_a_rotates(self):
        self.enterContext(mock.patch('time.time', return_value=float(int(time.time()))))
        generations = []
        for value in (None, self.other(), None):
            self.assertEqual(self.read(value)['state'], 'stored')
            generations.append(self.generation())
            self.assert_bound(generations[-1])
            self.assertEqual(codex_account.usable_windows(self.settings, int(time.time())), [])
        self.assertEqual(len(set(generations)), 3)
        self.assertEqual(self.spawns(), 3)

    def test_same_account_refresh_keeps_the_generation(self):
        self.read()
        first = self.generation()
        self.read()
        self.assert_bound(first)

    def test_explicit_refusal_or_observed_change_invalidates_continuity(self):
        for name, value in (('refused', codex_tests.payload(accounts=[codex_tests.identity(), dict(account=None)])),
                            ('changed', codex_tests.payload(accounts=[codex_tests.identity(),
                                                                      codex_tests.identity('other@example.test')]))):
            with self.subTest(name):
                self.read()
                first = self.generation()
                self.assertEqual(self.read(value)['state'], 'failed')
                self.assertIsNone(self.generation())
                self.assert_nothing_eligible()
                self.read()
                self.assertNotIn(self.generation(), (None, first))

    def test_transient_failure_keeps_only_proved_continuity(self):
        self.read()
        first = self.generation()
        self.assertEqual(self.read(codex_tests.payload(pages=[dict(data=[], nextCursor=None)]))['state'], 'failed')
        self.assertEqual(codex_account._state(self.settings)['account'], dict(pending=True, generation=first))
        self.assert_nothing_eligible()
        self.read()
        self.assert_bound(first)

    def test_legacy_and_old_writer_states_never_resurrect_a_generation(self):
        self.read()
        first = self.generation()
        key = codex_account._state(self.settings)['account']['key']
        legacy = dict(observed_at=int(time.time()), client_version='unknown',
                      usage_reason=codex_account.USAGE_UNCONFIRMED)
        for name, account in (('old A success', dict(legacy, key=key)), ('old B success', dict(legacy, key='b' * 32)),
                              ('old clear', None)):
            with self.subTest(name):
                codex_account._save(self.settings, account=account)
                self.assertEqual(codex_account._state(self.settings)['identity']['generation'], first)
                self.assert_nothing_eligible()
        self.read()
        self.assertNotIn(self.generation(), (None, first))

    def test_lost_and_recreated_store_establishes_a_fresh_generation(self):
        self.read()
        first = self.generation()
        codex_account._store(self.settings).unlink()
        self.assert_nothing_eligible()
        self.read()
        self.assertNotIn(self.generation(), (None, first))

    def test_warmed_observation_faults_never_resurrect_the_earlier_generation(self):
        for step in (1, 2, 3, 4):  # writes: 1 fence, 2 observed identity, 3 catalog, 4 complete account
            with self.subTest(step=step):
                self.read()
                first, spawns = self.generation(), self.spawns()
                with failing_write(step):
                    self.assertEqual(self.read(self.other())['state'], 'failed')
                self.assertEqual(self.spawns() - spawns, 0 if step == 1 else 1)
                if step == 1:
                    self.assert_bound(first)
                else:
                    self.assert_nothing_eligible()
                self.read()
                self.assertEqual(self.generation() == first, step == 1)

    def test_an_observed_account_with_an_invalid_catalog_breaks_continuity(self):
        self.read()
        first = self.generation()
        empty = codex_tests.payload(accounts=[codex_tests.identity('other@example.test')] * 2,
                                    pages=[dict(data=[], nextCursor=None)])
        self.assertEqual(self.read(empty)['state'], 'failed')
        self.assert_nothing_eligible()
        self.read()
        self.assertNotIn(self.generation(), (None, first))

    def test_a_failure_after_each_publication_step_exposes_nothing_early(self):
        # Writes: 1 fence, 2 identity, 3 catalog, 4 complete account.
        for step in (1, 2, 3, 4):
            with self.subTest(step=step):
                for path in (codex_account._store(self.settings), native_catalog._path(self.settings)):
                    path.unlink(missing_ok=True)
                with failing_write(step):
                    self.assertEqual(self.read()['state'], 'failed')
                self.assert_nothing_eligible()
                self.assertEqual(native_catalog.catalog(self.settings, 'codex')[1], 'native_binding_unverified')


if __name__ == '__main__':
    unittest.main()
