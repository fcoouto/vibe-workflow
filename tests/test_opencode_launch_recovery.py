"""0.1.44: the launcher's own attempt ids (C3, R3b N1) and an interrupted launch's recovery fields (C2, R4b F1).

Synthetic doubles only: tests/test_opencode_launcher.py's host double and the real routing command on temporary
stores. No account, client or network.
"""
import json
import unittest
from unittest import mock

from runtime import routing_launch
from tests import test_opencode_launcher as base

GENERATED = r'^n[0-9a-f]{24}$'
SENTINEL = 'prompt-sentinel-0144'
FIELDS = {'v', 'state', 'reason', 'attempt_id', 'uncertain', 'recovery', 'command'}


class CommandBase(base.LauncherBase):
    """`scripts/opencode_launch.py` as a coordinator runs it, on the host double and the test's own stores."""

    def run_main(self, assignment):
        from scripts import opencode_launch
        stdout = []
        request = dict(v=1, op='launch', checkout=str(self.checkout), assignment=assignment,
                       job=routing_launch.unplanned_job('software-engineer', base.SCOPE, SENTINEL), prompt=SENTINEL)
        server = mock.Mock(directory=str(self.checkout))
        server.request, server.rpc = self.host.request, self.host.rpc
        with mock.patch.object(opencode_launch.opencode_launcher, 'start_server', return_value=server), \
                mock.patch.object(opencode_launch.command, 'managed_service', return_value=self.service):
            code = opencode_launch.main([], stdin=json.dumps(request).encode(), write=stdout.append)
        self.assertNotIn(SENTINEL, stdout[0])  # never the prompt, the token or the server password
        return code, json.loads(stdout[0])

    def omitted(self):
        assignment = self.assignment()
        del assignment['attempt_id']
        return assignment


class GeneratedAttemptIdTests(CommandBase):
    def test_an_omitted_id_is_generated_launched_and_returned(self):
        code, result = self.run_main(self.omitted())
        self.assertEqual((code, result['state'], result['event']), (0, 'recorded', 'complete'), result)
        self.assertRegex(result['attempt_id'], GENERATED)
        self.assertEqual(self.holds()[result['attempt_id']]['state'], 'released')

    def test_two_launches_without_an_id_never_collide(self):
        (_, first), (_, second) = self.run_main(self.omitted()), self.run_main(self.omitted())
        self.assertEqual((first['state'], second['state']), ('recorded', 'recorded'), (first, second))
        self.assertNotEqual(first['attempt_id'], second['attempt_id'])

    def test_an_explicit_id_keeps_todays_answers(self):
        self.assertEqual(self.run_main(self.assignment())[1]['attempt_id'], 'a1')
        code, again = self.run_main(self.assignment())
        self.assertEqual((again['state'], again['reason'], again['attempt_id']), ('blocked', 'attempt_closed', 'a1'))


class InterruptedRecoveryTests(CommandBase):
    def run_main(self, assignment):
        code, answer = super().run_main(assignment)
        self.assertLessEqual(set(answer), FIELDS)  # fixed fields only
        return code, answer

    def test_an_interrupt_after_dispatch_names_the_uncertain_hold_and_its_operator_op(self):
        self.host.prompt_raises = KeyboardInterrupt()
        code, answer = self.run_main(self.assignment())
        self.assertEqual((code, answer), (1, dict(v=1, state='blocked', reason='interrupted', attempt_id='a1',
                                                  uncertain=True, recovery='reconcile_subscription',
                                                  command='scripts/routing.py reconcile_subscription')))
        self.assertEqual(self.holds()['a1']['state'], 'uncertain')

    def test_an_interrupt_before_dispatch_names_the_attempt_only(self):
        with mock.patch.object(routing_launch, 'admitted_check', side_effect=KeyboardInterrupt):
            code, answer = self.run_main(self.assignment())
        self.assertEqual((code, answer), (1, dict(v=1, state='blocked', reason='interrupted', attempt_id='a1',
                                                  uncertain=False)))
        self.assertEqual(self.holds()['a1']['state'], 'released')  # closed `not_dispatched`: nothing to reconcile

    def test_an_interrupt_before_admission_returns_the_generated_id(self):
        with mock.patch.object(base.launcher, 'catalog', side_effect=KeyboardInterrupt):
            code, answer = self.run_main(self.omitted())
        self.assertEqual((code, answer['state'], answer['reason'], answer['uncertain']), (1, 'blocked', 'interrupted',
                                                                                          False))
        self.assertRegex(answer['attempt_id'], GENERATED)
        self.assertEqual(self.holds(), {})

    def test_only_a_hold_left_uncertain_names_an_operator_op(self):
        gate = mock.Mock(attempt_id='a1', dispatch='dispatched')
        for recorded, uncertain in ((('recorded', None), True), (None, True), (('blocked', 'attempt_closed'), False)):
            with self.subTest(recorded=recorded), mock.patch.object(base.launcher, '_record', return_value=recorded):
                self.assertIs(base.launcher._interrupted(None, gate), uncertain)  # unknown stays uncertain


if __name__ == '__main__':
    unittest.main()
