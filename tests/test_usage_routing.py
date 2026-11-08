"""Local-only management and request-bound validation."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from runtime import usage_budget as budget, usage_routing as routing
from runtime.usage_budget import BudgetError


PROFILE = dict(model='example-model', reasoning='medium', tier='default', input_rate=4,
               cached_input_rate=2, cache_write_rate=8, output_rate=20,
               max_input_tokens=10000, max_output_tokens=1000,
               verified_at=100, valid_until=1000, count_maximum=0)
PACKET = dict(version=1, model='example-model', reasoning='medium', text='Synthetic offline text',
              input_tokens=1200, max_output_tokens=250)


class RoutingTests(unittest.TestCase):
    def test_status_off_never_creates_state_or_reads_credentials(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'OPENAI_API_KEY': 'must-not-read'}):
            with patch('socket.socket', side_effect=AssertionError('network forbidden')):
                result = routing.status(directory)
        self.assertEqual(result['mode'], 'off')
        self.assertFalse(result['live_dispatch'])
        self.assertFalse(result['review_authority'])

    def test_canonical_enrollment_is_orientation_without_live_readiness(self):
        from tests.fixture_canonical_birth import CanonicalBirthFixture
        owner = CanonicalBirthFixture('runTest')
        self.addCleanup(owner.doCleanups)
        fixture, _, _ = owner.accepted_birth(linked=True)
        observed = routing.status(fixture.repo)
        self.assertTrue(observed['enrolled'])
        self.assertFalse(observed['live_dispatch'])
        self.assertEqual(observed['enrollment'], 'configured')

    def test_live_enable_and_run_always_block(self):
        for operation in (routing.enable, routing.run):
            with self.assertRaisesRegex(BudgetError, 'live_execution_unavailable'):
                operation(None, {'enabled': True})

    def test_quote_uses_highest_input_rate_and_rounds_up(self):
        quote = routing.quote(PACKET, PROFILE, 101)
        self.assertEqual(quote['maximum_usd_micros'], 1)
        p = {**PROFILE, 'output_rate': 20_000_000}
        self.assertEqual(routing.quote(PACKET, p, 101)['maximum_usd_micros'], 5001)
        self.assertNotEqual(quote['digest'], routing.quote({**PACKET, 'text':'changed'}, PROFILE, 101)['digest'])

    def test_quote_rejects_unknown_fields_mismatch_staleness_and_overflows(self):
        cases = [({**PACKET, 'tools': []}, PROFILE, 101),
                 ({**PACKET, 'model':'other'}, PROFILE, 101),
                 (PACKET, {**PROFILE, 'tier':'auto'}, 101),
                 (PACKET, PROFILE, 1000), (PACKET, PROFILE, 99),
                 ({**PACKET, 'input_tokens':True}, PROFILE, 101),
                 ({**PACKET, 'max_output_tokens':1001}, PROFILE, 101),
                 (PACKET, {**PROFILE, 'count_maximum':None}, 101)]
        for packet, profile, now in cases:
            with self.subTest(packet=packet, profile=profile, now=now), self.assertRaises(BudgetError):
                routing.quote(packet, profile, now)

    def test_cli_status_and_quote_are_offline_and_bounded(self):
        command = [sys.executable, '-I', '-B', 'scripts/usage_routing.py']
        result = subprocess.run(command + ['status', '--cwd', '.'], capture_output=True, text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['mode'],'off')
        result = subprocess.run(command + ['quote'], input=json.dumps(dict(version=1,packet=PACKET,profile=PROFILE,now=101)), capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn('simulation_only', json.loads(result.stdout))
        for payload in ('x'*70000, '{"version":1,"version":1}'):
            result = subprocess.run(command + ['quote'], input=payload,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertNotIn('Traceback',result.stderr)


BINDING = dict(organization='org', project='project', credential_ref='OPENAI_API_KEY')
POLICY = dict(cycle='cycle1', start=0, end=10000, expiry=10000, stop_at=9000,
              ceiling='custom', amount=500, grant_amount=800, pilot_limit=500, task_limit=200)


def native(task):
    return dict(account='account', task=task, windows=[
        dict(id='weekly:1', remaining_pp=90, resolution_pp=1, allowance_pp=10, observed_at=100, reset_at=10000)])


def candidate(name, **updates):
    value = dict(task_id=name, run_id=name, class_name='investigation', packet=PACKET.copy(),
                 provider='auto', local_preferred=False, api_only=False, reviewer_available=True,
                 native=native(name), snapshots=[])
    value.update(updates)
    return value


class SlotFreeRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name) / 'private' / 'ledger'
        self.identity = budget.initialize(path)
        self.ledger = budget.Ledger(path, self.identity)
        self.controller = routing.Controller(self.ledger, 'scope', clock=lambda: 101)
        self.controller.configure(dict(version=1, expected_revision=0, expected_org_revision=0,
                                       binding=BINDING, policy=POLICY, profile=PROFILE,
                                       class_name='investigation', mode='smart'))
        self.ledger.observe('org', 800, 100)

    def tearDown(self):
        self.ledger.close(); self.temp.cleanup()

    def test_evaluate_admits_an_affordable_second_task_while_another_is_unresolved(self):
        self.ledger.begin('org', 'first', 'first', 'packet', 10, 1, 101, native('first'),
                          consent=self.controller._consent(self.controller._config(self.ledger.snapshot()), False))
        self.ledger.dispatch('first', 'count', 1, 102)
        self.ledger.uncertain('first', 'count')
        self.assertNotIn('slot', self.ledger.snapshot())
        decision = self.controller.evaluate(candidate('second'))
        self.assertEqual((decision['decision'], decision['reason']), ('eligible', 'qualified_profile_and_budget'))
        self.assertNotIn('request_in_flight', routing.HANDOFF_REASONS)

    def test_evaluate_still_pauses_a_second_task_that_does_not_fit_shared_headroom(self):
        self.ledger.set_caps(total=10, per_account=10)
        for name, maximum in (('first', 200), ('other', 200), ('rest', 100)):
            self.ledger.begin('org', name, name, 'packet', maximum, 1, 101, native(name))
        decision = self.controller.evaluate(candidate('second'))
        self.assertEqual((decision['decision'], decision['reason']), ('paused', 'insufficient_headroom'))

    def test_reconcile_enumerates_every_pending_attempt_in_scope(self):
        self.ledger.set_caps(total=10, per_account=10)
        consent = self.controller._consent(self.controller._config(self.ledger.snapshot()), False)
        for name in ('first', 'second'):
            self.ledger.begin('org', name, name, 'packet', 10, 1, 101, native(name), consent=consent)
            self.ledger.dispatch(name, 'count', 1, 102)
            self.ledger.uncertain(name, 'count', incomplete='timeout')
        self.ledger.begin('org', 'reserved-only', 'reserved-only', 'packet', 10, 1, 101, native('reserved-only'), consent=consent)
        self.ledger.begin('org', 'foreign', 'foreign', 'packet', 10, 1, 101, native('foreign'))  # No scope.
        self.ledger.dispatch('foreign', 'count', 1, 102); self.ledger.uncertain('foreign', 'count')
        result = self.ledger.reconcile(scope='scope')
        self.assertEqual(result['state'], 'manual_recovery_required')
        self.assertEqual([item['attempt_id'] for item in result['pending_attempts']],
                         ['first:count', 'second:count'])
        self.assertEqual(result['pending_attempts'][0], dict(attempt_id='first:count', reserved_usd_micros=10,
                                                              incomplete='timeout'))
        self.assertEqual(result['pending'], result['pending_attempts'][0])
        self.assertEqual(self.ledger.reconcile(scope='nobody'), {'state': 'manual_recovery_required'})

    def test_unenabled_status_reports_no_dispatch_and_plain_reason(self):
        status = self.controller.status()
        self.assertFalse(status['live_dispatch'])
        self.assertFalse(status['enabled'])
        self.assertEqual(status['reason'], 'live_adapter_unavailable')
        self.assertNotIn('live_blocker', status)


if __name__ == '__main__': unittest.main()
