import json
import os
from pathlib import Path
import tempfile
import unittest

from runtime import routing_settings, usage_budget


CHOICE = dict(provider='p', model='m', effort='e', harness='h', funding='subscription')


class RoutingSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name).resolve() / 'routing'
        self.path = folder / 'settings.json'

    def test_defaults(self):
        value = routing_settings.resolve({})
        self.assertIs(value['smart_routing'], True)
        self.assertIs(value['api_usage_enabled'], False)
        self.assertEqual((value['L'], value['H'], value['T_hours']), (0.65, 0.85, 72))
        self.assertEqual((value['min_elapsed_fraction'], value['lookback_hours'], value['margin_bp']),
                         (0.10, 72, usage_budget.DEFAULT_MARGIN_BP))
        self.assertEqual(value['caps'], usage_budget.DEFAULT_CAPS)
        self.assertEqual(value['worker_choices'], {})

    def test_explicit_off_survives_write_and_read(self):
        record = routing_settings.update(self.path, dict(smart_routing=False), 0)
        self.assertEqual(record['revision'], 1)
        self.assertIs(routing_settings.read(self.path)['smart_routing'], False)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_smart_routing_does_not_change_other_enablement(self):
        routing_settings.update(self.path, dict(smart_routing=True), 0)
        value = routing_settings.read(self.path)
        self.assertIs(value['api_usage_enabled'], False)
        self.assertEqual(set(value) & {'openrouter', 'council', 'observability'}, set())

    def test_api_usage_is_changed_only_by_its_own_field(self):
        routing_settings.update(self.path, dict(smart_routing=False), 0)
        self.assertIs(routing_settings.read(self.path)['api_usage_enabled'], False)
        routing_settings.update(self.path, dict(api_usage_enabled=True), 1)
        self.assertIs(routing_settings.read(self.path)['api_usage_enabled'], True)
        self.assertIs(routing_settings.read(self.path)['smart_routing'], False)

    def test_threshold_invariants(self):
        for bad in (dict(L=0.9, H=0.8), dict(L=0.5, H=0.5), dict(L=-0.1), dict(H=1.1), dict(T_hours=0),
                    dict(T_hours=-5), dict(min_elapsed_fraction=1.5), dict(margin_bp=0), dict(L=True)):
            with self.subTest(bad), self.assertRaises(routing_settings.SettingsError):
                routing_settings.update(self.path, bad, 0)
        self.assertEqual(routing_settings.update(self.path, dict(L=0, H=1), 0)['revision'], 1)

    def test_caps_use_ledger_field_names(self):
        routing_settings.update(self.path, dict(caps=dict(total=6, per_account=3)), 0)
        self.assertEqual(routing_settings.read(self.path)['caps'], dict(total=6, per_account=3))
        for bad in (dict(total=0, per_account=1), dict(total=1), dict(total=1, per_account=1, x=1)):
            with self.subTest(bad), self.assertRaises(routing_settings.SettingsError):
                routing_settings.update(self.path, dict(caps=bad), 1)

    def test_worker_choices_validated(self):
        routing_settings.update(self.path, dict(worker_choices=dict(implementer=[CHOICE])), 0)
        self.assertEqual(routing_settings.read(self.path)['worker_choices'], dict(implementer=[CHOICE]))
        missing = {k: v for k, v in CHOICE.items() if k != 'harness'}
        for bad in (dict(planner=[CHOICE]), dict(reviewer=[missing]), dict(reviewer=[dict(CHOICE, extra=1)]),
                    dict(reviewer=CHOICE), dict(reviewer=[dict(CHOICE, funding='free-money')]),
                    dict(reviewer=[dict(CHOICE, model='')])):
            with self.subTest(bad), self.assertRaises(routing_settings.SettingsError):
                routing_settings.update(self.path, dict(worker_choices=bad), 1)

    def test_all_roles_accepted_and_order_kept(self):
        second = dict(CHOICE, model='n')
        choices = {role: [CHOICE, second] for role in ('implementer', 'reviewer', 'researcher', 'repair')}
        routing_settings.update(self.path, dict(worker_choices=choices), 0)
        self.assertEqual(routing_settings.read(self.path)['worker_choices'], choices)

    def test_duplicate_worker_choices_rejected(self):
        with self.assertRaises(routing_settings.SettingsError):
            routing_settings.update(self.path, dict(worker_choices=dict(reviewer=[CHOICE, dict(CHOICE)])), 0)

    def test_intermediate_directories_are_private(self):
        deep = Path(self.tmp.name).resolve() / 'a' / 'b' / 'settings.json'
        routing_settings.update(deep, dict(smart_routing=False), 0)
        for folder in (deep.parent, deep.parent.parent):
            self.assertEqual(os.stat(folder).st_mode & 0o777, 0o700)

    def test_stale_revision_refused(self):
        routing_settings.update(self.path, dict(smart_routing=False), 0)
        with self.assertRaises(routing_settings.SettingsError) as caught:
            routing_settings.update(self.path, dict(smart_routing=True), 0)
        self.assertEqual(str(caught.exception), 'stale_revision')
        self.assertIs(routing_settings.read(self.path)['smart_routing'], False)

    def test_unknown_field_rejected(self):
        with self.assertRaises(routing_settings.SettingsError):
            routing_settings.update(self.path, dict(openrouter=True), 0)

    def test_unsafe_storage_refused(self):
        routing_settings.update(self.path, dict(smart_routing=False), 0)
        os.chmod(self.path, 0o644)
        with self.assertRaises(usage_budget.BudgetError):
            routing_settings.read(self.path)

    def test_corrupt_storage_refused(self):
        routing_settings.update(self.path, dict(smart_routing=False), 0)
        self.path.write_text(json.dumps(dict(version=1, revision=1, values=dict(L=5))))
        os.chmod(self.path, 0o600)
        with self.assertRaises(routing_settings.SettingsError):
            routing_settings.read(self.path)

    def test_missing_file_reads_defaults_at_revision_zero(self):
        self.assertEqual(routing_settings.revision(self.path), 0)
        self.assertIs(routing_settings.read(self.path)['smart_routing'], True)

    def test_resolve_rejects_invalid_stored(self):
        with self.assertRaises(routing_settings.SettingsError):
            routing_settings.resolve(dict(L=0.9, H=0.1))

    def test_workstreams_preference_is_read_not_copied(self):
        calls = []
        scope = object()
        loader = lambda s: calls.append(s) or dict(enabled=True)
        self.assertIs(routing_settings.workstreams_enabled(scope, load=loader), True)
        self.assertEqual(calls, [scope])
        routing_settings.update(self.path, dict(smart_routing=False), 0)
        self.assertNotIn('workstreams', json.loads(self.path.read_text()).get('values', {}))


if __name__ == '__main__':
    unittest.main()
