"""Local-only management and request-bound validation."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
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


class Reached(BaseException):
    """A tripwire: no handler in the code under test catches it, so a late refusal cannot hide it."""


RETIRED = 'legacy_route_retired'
QUALIFICATION = dict(class_name='investigation', profile_digest=routing.profile_digest(PROFILE), samples=4,
                     quality_passed=True, net_native_saved=5, uncertainty=1, unit='allowance_pp',
                     native_calibrated=True, observed_at=100, valid_until=900, native_allowance_pp=7)


class ManagedProjectRefusalTests(unittest.TestCase):
    """C.3 design 4.3 and A.2: every legacy API Usage spending entry refuses a managed project first."""

    def setUp(self):
        from runtime import routing_settings, usage_settings
        from scripts import routing as command
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.checkout = self.root / 'checkout'
        self.checkout.mkdir()
        self.settings = self.root / 'routing' / 'settings.json'
        for target, attribute, value in ((command, 'default_settings', lambda: self.settings),
                                         (usage_settings, 'directory', lambda: self.root / 'usage')):
            patcher = patch.object(target, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        routing_settings.update(self.settings, dict(managed_projects=[str(self.checkout)]), 0)

    def tripwires(self):
        """Fail on any settings, credential, configuration or ledger access a refused entry must not reach."""
        from runtime import usage_canary_host, usage_codex_host, usage_credentials, usage_settings
        for target, attribute in ((budget.Ledger, 'begin'), (usage_settings, 'controller'), (usage_settings, 'scope'),
                                  (routing.Controller, 'configure'), (usage_credentials, 'validate_binding'),
                                  (usage_credentials, 'resolve_key'), (usage_codex_host, 'package_digest'),
                                  (usage_canary_host, 'package_digest'), (usage_canary_host, 'verify_approval')):
            patcher = patch.object(target, attribute, side_effect=Reached(attribute))
            patcher.start()
            self.addCleanup(patcher.stop)

    def entries(self):
        import dataclasses
        from runtime import usage_admission, usage_canary, usage_canary_host, usage_codex_host

        def blank(kind, **values):
            return kind(**({item.name: None for item in dataclasses.fields(kind)} | values))
        checkout, task = str(self.checkout), candidate('managed')
        evidence = blank(usage_admission.Admission)
        return dict(
            codex_freeze=lambda: usage_codex_host.freeze(checkout, [task], thread_snapshot={}, reviewer=None,
                                                         expires_at=200),
            codex_execute=lambda: usage_codex_host.execute(blank(usage_codex_host.Plan, checkout=checkout), task,
                                                           thread_snapshot={}, reviewer=None, grant=None, price=None),
            canary_host_execute=lambda: usage_canary_host.execute(
                blank(usage_canary_host.Plan, checkout=checkout), thread_snapshot={}, key_receipt={}, grant=None,
                reviewer=None, price_observed_at=100, price_valid_until=1000),
            canary_run=lambda: usage_canary.run(checkout, evidence, binding=BINDING, profile=PROFILE,
                                                native=native('managed')),
            run_live=lambda: routing._run_live(checkout, task, evidence),
            evaluate=lambda: routing.evaluate(checkout, task),
            handoff=lambda: routing.handoff(checkout, dict(task=task, reason='insufficient_headroom')),
            claim_handoff=lambda: routing.claim_handoff(checkout, dict(task_id='managed', attempt_id='attempt')))

    def assert_every_entry_refuses(self):
        for entry, call in self.entries().items():
            with self.subTest(entry=entry), self.assertRaisesRegex(BudgetError, '^' + RETIRED + '$'):
                call()

    def test_every_spending_entry_refuses_before_settings_credentials_configuration_or_ledger(self):
        self.tripwires()
        self.assert_every_entry_refuses()

    def test_a_path_inside_the_managed_checkout_is_refused_too(self):
        from runtime import usage_canary
        self.tripwires()
        with self.assertRaisesRegex(BudgetError, RETIRED):
            usage_canary.run(str(self.checkout / 'nested' / 'new'), None, binding=BINDING, profile=PROFILE,
                             native=native('managed'))

    def test_an_unreadable_routing_settings_file_counts_as_managed(self):
        self.settings.write_text('{not json')
        self.tripwires()
        self.assert_every_entry_refuses()

    def test_unmanaged_projects_pass_the_refusal_unchanged(self):
        from runtime import routing_settings
        other = self.root / 'other'
        other.mkdir()
        routing_settings.update(self.settings, dict(managed_projects=[str(other)]), 1)
        self.tripwires()
        for entry, call in self.entries().items():
            with self.subTest(entry=entry):
                try:
                    call()
                except BudgetError as error:
                    self.assertNotEqual(str(error), RETIRED)
                except Reached:
                    pass
        self.settings.unlink()  # no routing settings at all: nothing is managed
        with self.assertRaises(Reached):
            routing.evaluate(str(self.checkout), candidate('managed'))

    def enabled_controller(self):
        """Configure and enable legacy API Usage in the managed checkout through the setup actions."""
        from contextlib import contextmanager
        from runtime import usage_settings
        path = self.root / 'usage' / 'organizations.sqlite3'
        path.parent.mkdir(mode=0o700)
        ledger = budget.Ledger(path, budget.initialize(path))
        self.addCleanup(ledger.close)
        control = routing.Controller(ledger, 'scope', root=self.checkout, clock=lambda: 101)

        @contextmanager
        def local(*args, **kwargs):
            yield control
        with patch.object(usage_settings, 'controller', side_effect=local):
            configured = routing.configure(str(self.checkout), dict(
                version=1, expected_revision=0, expected_org_revision=0, binding=BINDING, policy=POLICY,
                profile=PROFILE, class_name='investigation', mode='smart'))
            with ledger.transaction() as data:
                data['routing']['scope'].update(qualification=dict(QUALIFICATION), qualification_revision=1)
            enabled = routing.enable(str(self.checkout), dict(expected_revision=configured['revision']))
            self.assertIsInstance(routing.status(str(self.checkout)), dict)
        self.assertTrue(enabled['enabled'])
        ledger.observe('org', 800, 100)
        return control

    def test_setup_actions_stay_available_and_enabling_reaches_no_ledger_begin(self):
        control = self.enabled_controller()
        self.tripwires()
        self.assert_every_entry_refuses()
        self.assertEqual(control.ledger.snapshot()['runs'], {})

    def test_controller_backstops_refuse_before_any_decision_or_ledger_begin(self):
        control = self.enabled_controller()
        self.tripwires()
        task = candidate('managed')
        backstops = dict(
            run=lambda: control._run(task, lambda *_: None, lambda _: b'synthetic-key'),
            evaluate=lambda: control._evaluate(control.ledger.snapshot(), task),
            request_handoff=lambda: control.request_handoff(task, 'insufficient_headroom'))
        for entry, call in backstops.items():
            with self.subTest(entry=entry), self.assertRaisesRegex(BudgetError, '^' + RETIRED + '$'):
                call()
        self.assertEqual(control.evaluate(task)['reason'], RETIRED)
        self.assertEqual(control.ledger.snapshot()['runs'], {})
        self.assertEqual(control.ledger.snapshot().get('handoffs', {}), {})

    def test_cli_spending_actions_report_the_refusal_and_run_stays_unavailable(self):
        from scripts import usage_routing as cli
        self.tripwires()
        task = candidate('managed')
        for action, request, reason in (('evaluate', task, RETIRED),
                                        ('handoff', dict(task=task, reason='insufficient_headroom'), RETIRED),
                                        ('claim_handoff', dict(task_id='managed', attempt_id='a'), RETIRED),
                                        ('run', task, 'live_execution_unavailable')):
            output = io.StringIO()
            stdin = SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()))
            with self.subTest(action=action), patch.object(sys, 'argv', ['usage_routing.py', action, '--cwd',
                                                                         str(self.checkout)]), \
                    patch.object(sys, 'stdin', stdin), patch.object(sys, 'stdout', output):
                self.assertEqual(cli.main(), 1)
                self.assertEqual(json.loads(output.getvalue()), dict(ok=False, reason=reason))

    def test_the_activation_self_check_passes_and_names_an_entry_that_refuses_late(self):
        from runtime import usage_canary
        from scripts import routing as command
        self.assertEqual(routing.managed_self_check(str(self.root / 'check')), [])
        self.assertEqual(command._self_check('module', 'runtime.usage_routing:managed_self_check'), [])
        self.assertEqual(sorted(path.name for path in (self.root / 'check').iterdir()), ['check', 'home', 'tmp'])
        self.assertEqual(list((self.root / 'check' / 'home').iterdir()), [])
        original = usage_canary.run

        def late(cwd, evidence, **options):  # the refusal moved after a credential-binding read
            usage_canary.usage_credentials.validate_binding(options['binding'])
            return original(cwd, evidence, **options)
        with patch.object(usage_canary, 'run', late):
            self.assertEqual(routing._self_check_body(str(self.root / 'late')), ['canary_run_not_refused_first'])
        self.assertEqual(routing.managed_self_check(str(self.root)), ['self_check_workdir_unsafe'])

    def test_old_consent_is_never_api_spending_authority(self):
        """Spec 10.4 step 4: enabled legacy Usage Routing, current consent and a qualification record, with
        default routing settings, leave every API route pending with `api_usage_disabled` and read no secret."""
        import time
        from runtime import routing_launch, routing_observations, usage_credentials
        from scripts import routing as command
        control = self.enabled_controller()
        now = int(time.time())
        with control.ledger.transaction() as data:
            data['organizations']['org']['observed_at'] = now
            consent = control._consent(control._config(data), True)
        self.assertEqual(consent['qualification_revision'], 1)
        identity = routing_launch.route_identity('api_usage', model=PROFILE['model'], effort=PROFILE['reasoning'])
        scope = routing_launch.assignment_scope(self.checkout)
        source = routing_observations.StaticSource(
            [dict(route='api-grant', status='qualified', billing_class='api_grant', pool='api', account='org',
                  demand={'credit': 1}, **identity)],
            {'api': [dict(id='credit', limit=10 ** 12, used=0, organization='org', rolling=False, observed_at=now)]},
            credential=lambda route, scope: dict(state='ready', scope=scope, ref='org'))
        for target, attribute in ((usage_credentials, 'resolve_key'), (usage_credentials, '_read_key')):
            patcher = patch.object(target, attribute, side_effect=Reached(attribute))
            patcher.start()
            self.addCleanup(patcher.stop)
        service = command.Service(self.settings, control.ledger.path, project=self.checkout)
        self.addCleanup(service.close)
        with patch.object(routing_observations, 'default_source', return_value=source):
            for attempt, choice in (('routed', None), ('chosen', dict(identity, funding='api'))):
                assignment = dict(attempt_id=attempt, task='task-' + attempt, role='implementer', scope=scope)
                if choice is not None:
                    assignment['choice'] = choice
                request = routing_launch.prepare_request(
                    assignment, routing_launch.unplanned_job('implementer', scope, 'managed proposal'))
                with self.subTest(attempt=attempt):
                    result = service.handle(json.dumps(request).encode())
                    self.assertEqual(result['state'], 'pending', result)
                    self.assertIn('api_usage_disabled', json.dumps(result))
        self.assertFalse(service._settings()['api_usage_enabled'])


if __name__ == '__main__': unittest.main()
