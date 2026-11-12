"""Foreground host receipt checks with synthetic Desktop responses."""
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime import usage_admission, usage_budget, usage_canary, usage_canary_host as host, usage_routing
from runtime.usage_budget import BudgetError
from tests.test_openai_api import Connection, Response, completed


class CanaryHostTests(unittest.TestCase):
    def setUp(self):
        # The compiled table's cheapest priced model for the canary's provider (an invented name).
        cheapest = patch('runtime.usage_canary.model_names.cheapest', return_value='vendor-light')
        cheapest.start()
        self.addCleanup(cheapest.stop)
        self.plan = host.Plan(
            thread_id='thread', host_id='desktop', checkout='/fixture/project', scope='scope',
            package_digest='a' * 64, account='account', reviewer='independent-reviewer',
            organization='org-fixture', project='proj_fixture', key_name='Codex',
            key_expiry=None, grant_expiry=10_000, grant_amount=270_000_000, created_at=100, expires_at=200,
        )
        self.message = dict(type='userMessage', id='user-item',
                            content=[dict(type='text', text=host.approval_text(self.plan))])
        self.snapshot = dict(thread=dict(kind='codex', id='thread', hostId='desktop', cwd='/fixture/project'),
                             turns=[dict(id='turn', status='inProgress', startedAt=105, items=[self.message])])
        self.binding = dict(organization='org-fixture', project='proj_fixture', storage_kind='macos_keychain',
                            credential_ref='vcw_' + '0' * 32)
        self.receipt = dict(selection=dict(name='Codex', organization_id='org-fixture', project_id='proj_fixture'),
                            mode='off', binding=self.binding)
        self.config = dict(enabled=True, disabled=False, requested_mode='smart', revision=2, org_revision=1,
                           class_name='canary', binding=self.binding,
                           profile=usage_canary.profile_for(101, 200))
        cycle = 'canary-' + usage_admission.digest(asdict(self.plan))
        self.policy = dict(cycle=cycle, start=90, end=10_000, expiry=10_000, stop_at=900,
                           ceiling='subscription_benefit', amount=None, grant_amount=270_000_000,
                           pilot_limit=50_640, task_limit=50_640)
        self.limits = dict(accountId='account', ordinaryUsageAllowed=True, rateLimitsByLimitId={
            'codex': dict(limitId='codex', spendControlReached=False, secondary=None,
                          primary=dict(usedPercent=48.1, windowDurationMins=10080, resetsAt=900))})
        self.grant = usage_admission.Grant('org-fixture', 'proj_fixture', 270_000_000, 10_000, 105,
                                           'platform-credit-grants', 'benefit')

    def issue(self, **overrides):
        args = dict(thread_snapshot=self.snapshot, key_receipt=self.receipt, config=self.config,
                    policy=self.policy, grant=self.grant, native_limits=self.limits,
                    native_observed_at=105, reviewer='independent-reviewer', scope='scope',
                    package_digest='a' * 64, now=106)
        args.update(overrides)
        return host.issue(self.plan, **args)

    def test_exact_foreground_approval_binds_one_canary(self):
        evidence, native = self.issue()
        task = usage_canary.task_for(evidence.approval_id, self.binding, native)
        usage_canary.validate(evidence, binding=self.binding, profile=self.config['profile'], task=task, now=106)
        self.assertEqual(native['windows'], [])
        self.assertEqual(evidence.approval_until, 200)
        self.message['id'] = 'another-direct-message'
        repeated, _ = self.issue()
        self.assertEqual(repeated.approval_id, evidence.approval_id)

    def test_native_approval_allows_one_terminal_line_feed(self):
        token = host.approval_text(self.plan)
        self.message['content'][0]['text'] = token + '\n'
        self.assertEqual(host.verify_approval(self.plan, self.snapshot, 106), 105)
        for text in (token + '\n\n', token + ' ', token + '\r\n', '\n' + token, token + '\nextra'):
            with self.subTest(text=text), self.assertRaisesRegex(BudgetError, 'canary_approval_missing'):
                self.message['content'][0]['text'] = text
                host.verify_approval(self.plan, self.snapshot, 106)

    def test_refuses_wrong_role_text_host_and_stale_turn(self):
        self.message['type'] = 'agentMessage'
        with self.assertRaises(BudgetError):
            self.issue()
        self.message['type'] = 'userMessage'
        self.message['content'][0]['text'] += ' and another test'
        with self.assertRaises(BudgetError):
            self.issue()
        self.message['content'][0]['text'] = host.approval_text(self.plan)
        self.snapshot['thread']['hostId'] = 'other-host'
        with self.assertRaises(BudgetError):
            self.issue()
        self.snapshot['thread']['hostId'] = 'desktop'
        self.snapshot['turns'][0]['startedAt'] = 99
        with self.assertRaises(BudgetError):
            self.issue()

    def test_refuses_candidate_funding_and_key_changes(self):
        for change in (dict(package_digest='b' * 64), dict(grant=replace(self.grant, pool='mixed')),
                       dict(reviewer='other-worker'), dict(now=201),
                       dict(key_receipt=dict(self.receipt, selection=dict(self.receipt['selection'],
                                                                       expires_in_seconds=86400)))):
            with self.subTest(change=change), self.assertRaises(BudgetError):
                self.issue(**change)

    def test_canary_ignores_unavailable_or_changed_quota(self):
        for limits in (None, dict(self.limits, accountId='other'), dict(self.limits, ordinaryUsageAllowed=False),
                       dict(self.limits, rateLimitsByLimitId={}), dict(self.limits, rateLimitsByLimitId={
                           **self.limits['rateLimitsByLimitId'], 'unknown': {}})):
            evidence, native = self.issue(native_limits=limits)
            self.assertEqual(native['windows'], [])
            self.assertEqual(evidence.reviewer, self.plan.reviewer)

    @contextmanager
    def controller(self):
        root = Path(self.plan.checkout)
        with tempfile.TemporaryDirectory() as temporary:
            ledger_path = Path(temporary) / 'private' / 'ledger'
            with usage_budget.Ledger(ledger_path, usage_budget.initialize(ledger_path)) as ledger:
                control = usage_routing.Controller(ledger, 'scope', root=root, clock=lambda: 106)

                @contextmanager
                def settings(*args, **kwargs):
                    yield control

                with patch.object(host.time, 'time', return_value=106), patch.object(
                        host, 'package_digest', return_value=self.plan.package_digest), patch.object(
                        host.usage_settings, 'scope', return_value=('scope', root)), patch.object(
                        host.usage_settings, 'controller', side_effect=settings):
                    yield control

    def execute(self):
        return host.execute(
            self.plan, thread_snapshot=self.snapshot, key_receipt=self.receipt, grant=self.grant,
            native_limits=self.limits, native_observed_at=105, reviewer=self.plan.reviewer,
            price_observed_at=101, price_valid_until=200)

    def test_execution_failure_restores_off_and_same_plan_cannot_retry(self):
        with self.controller() as control:
            def reject(*args, **kwargs):
                state = control.status()
                self.assertEqual((state['mode'], state['canary_active']), ('off', True))
                self.assertIsNone(state['activation'])
                raise BudgetError('fixture_transport_error')

            with patch.object(usage_canary, 'run', side_effect=reject) as run:
                with self.assertRaisesRegex(BudgetError, 'fixture_transport_error'):
                    self.execute()
                state = control.status()
                self.assertEqual((state['mode'], state['canary_active']), ('off', False))
                with self.assertRaisesRegex(BudgetError, 'canary_state_already_configured'):
                    self.execute()
                run.assert_called_once()

    def test_enable_commit_then_failure_still_restores_off(self):
        with self.controller() as control:
            transaction = control.ledger.transaction
            failed = False

            @contextmanager
            def fail_after_enable(*args, **kwargs):
                nonlocal failed
                with transaction(*args, **kwargs) as data:
                    yield data
                    enabled = data.get('routing', {}).get('scope', {}).get('enabled', False)
                if enabled and not failed:
                    failed = True
                    raise BudgetError('fixture_after_commit')

            with patch.object(control.ledger, 'transaction', side_effect=fail_after_enable), patch.object(
                    usage_canary, 'run') as run:
                with self.assertRaisesRegex(BudgetError, 'fixture_after_commit'):
                    self.execute()
                run.assert_not_called()
            self.assertEqual(control.status()['mode'], 'off')

    def test_registered_canary_refuses_its_buffered_task_before_any_send(self):
        """F5.A2: the registered maximum (50,640) equals the canary's unbuffered quote, so its whole-task envelope
        (ceil(50,640 x 1.2) = 60,768) cannot fit. The approved maximum and packet stay unchanged: the canary refuses
        offline before any transport, credential, monetary or native claim, restores off and stays consumed. The
        earlier successful rehearsals remain historical evidence; fake success is covered by the dormant runner."""
        with self.controller() as control:
            def dispatch(cwd, task, evidence):
                changed = dict(task, packet=dict(task['packet'], text='Different task.'))
                changed_evidence = replace(evidence, task_digest=usage_admission.digest(changed))
                with self.assertRaisesRegex(BudgetError, 'routing_budget_required'):
                    control._run_live(changed, changed_evidence, connection_factory=lambda *args: self.fail('sent'),
                                      credential_resolver=lambda binding: self.fail('credential resolved'))
                return control._run_live(task, evidence, connection_factory=lambda *args: self.fail('sent'),
                                         credential_resolver=lambda binding: self.fail('credential resolved'))

            with patch.object(usage_routing, '_run_live', side_effect=dispatch) as run:
                with self.assertRaisesRegex(BudgetError, 'insufficient_headroom'):
                    self.execute()
                run.assert_called_once()
                data = control.ledger.snapshot()
                self.assertEqual((data['runs'], data['native'], data['holds']), ({}, {}, {}))
                self.assertEqual(budget_total(data, self.plan.organization), 0)
                state = control.status()
                self.assertEqual((state['mode'], state['canary_active']), ('off', False))
                with self.assertRaisesRegex(BudgetError, 'canary_state_already_configured'):
                    self.execute()
                run.assert_called_once()

    def test_production_reconfiguration_after_a_canary_preserves_its_history(self):
        """Synthetic history (not the registered canary's execution): a later production budget configure and enable
        keep earlier runs, native tasks and the organization record, with only revision and policy replaced."""
        with self.controller() as control:
            refuse = lambda cwd, task, evidence: control._run_live(  # noqa: E731  stages the grant, then refuses
                task, evidence, connection_factory=lambda *args: self.fail('sent'),
                credential_resolver=lambda binding: self.fail('credential resolved'))
            with patch.object(usage_routing, '_run_live', side_effect=refuse):
                with self.assertRaisesRegex(BudgetError, 'insufficient_headroom'):
                    self.execute()
            native = dict(account='account', task='history', windows=[])
            control.ledger.begin(self.plan.organization, 'history', 'history', 'packet', 100, 1, 106, native)
            control.ledger.dispatch('history', 'count', 1, 106)
            control.ledger.complete('history', 'count', 80)
            control.ledger.close_run('history')
            data = control.ledger.snapshot()
            old_org = data['organizations'][self.plan.organization]
            request = dict(version=2, expected_revision=3, expected_org_revision=1, binding=self.binding,
                           profile=self.config['profile'], class_name='proposal', mode='smart',
                           policy=dict(old_org['policy'], pilot_limit=1_000_000, task_limit=100_000, stop_at=900))
            configured = control.configure(dict(request, production_budget=True))
            self.assertEqual((configured['mode'], configured['revision']), ('off', 4))
            self.assertTrue(control.enable(4)['live_enabled'])
            after = control.ledger.snapshot()
            self.assertEqual(after['runs'], data['runs'])
            self.assertEqual(after['native'], data['native'])
            new_org = after['organizations'][self.plan.organization]
            self.assertEqual(new_org, dict(old_org, revision=2, policy=request['policy']))
            refreshed = control.configure(dict(request, expected_revision=5, expected_org_revision=2))
            self.assertEqual(refreshed['mode'], 'off')
            self.assertTrue(control.enable(6)['live_enabled'])


def budget_total(data, organization):
    return usage_budget.Ledger._total(data, organization) if organization in data['organizations'] else 0

if __name__ == '__main__':
    unittest.main()
