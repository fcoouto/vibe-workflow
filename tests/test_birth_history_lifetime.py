"""Birth-history owner lifetime controls; synthetic clock faults, no native proof."""
import copy
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from runtime import evidence, review_storage, state
from tests import fixture_canonical_birth as canonical_birth


class BirthHistoryLifetimeTests(unittest.TestCase):
    def cleanup_case(self, case):
        for index, (callback, args, kwargs) in enumerate(case._cleanups):
            child = getattr(callback, '__self__', None)
            if isinstance(child, unittest.TestCase) and getattr(callback, '__name__', '') == 'doCleanups':
                self.assertFalse(args or kwargs)
                case._cleanups[index] = (self.cleanup_case, (child,), {})
        self.assertTrue(case.doCleanups(), 'Nested canonical birth cleanup failed')

    def source_snapshot(self, project_context, authority_path, history):
        configuration = history['machine']['configuration']
        temporary_context = evidence._reference_context(configuration['authority'])
        receipts = {}
        for context in (project_context, temporary_context):
            rows = {}
            with evidence._context_database(context) as db:
                for row in evidence._evidence_inventory_rows(db):
                    metadata, raw = evidence._managed_artifact(None, row['reference'], context=context, db=db)
                    rows[evidence._raw(row['reference'])] = (metadata, raw)
            receipts[str(context.database)] = rows
        paths = {authority_path, Path(history['machine']['path'])}
        paths.update(Path(source['pointer']['plan_directory']) / 'plan.json'
                     for source in history['screening_sources'])
        files = {}
        for path in paths:
            info = path.stat()
            files[str(path)] = (info.st_dev, info.st_ino, info.st_mode, path.read_bytes())
        return dict(receipts=receipts, files=files)

    def test_expired_birth_history_callback_cannot_renew_or_outlive_owner(self):
        birth = canonical_birth.CanonicalBirthFixture('runTest')
        self.addCleanup(self.cleanup_case, birth)
        fixture, acquired, _ = birth.accepted_birth()
        authority_path = acquired.resolution.scope.authority_path
        record = json.loads(authority_path.read_bytes())
        context = birth.context(fixture)
        original_budget = review_storage._budget
        observed_budgets = []

        def record_budget(*args, **kwargs):
            budget = original_budget(*args, **kwargs)
            observed_budgets.append(budget)
            return budget

        with patch.object(review_storage, '_budget', side_effect=record_budget):
            with review_storage._capture_birth_history(record) as (history, reobserve):
                owner = review_storage._MACHINE_ACQUISITION.get()
                self.assertIsNotNone(owner)
                self.assertTrue(owner['active'])
                budget = observed_budgets[-1]
                deadline = budget['deadline']
                original_history = copy.deepcopy(history)
                before = self.source_snapshot(context, authority_path, history)
                # Semantic expiry fault only; this does not extend any production deadline.
                with patch.object(review_storage.time, 'monotonic', return_value=deadline + 1):
                    with self.assertRaises(subprocess.TimeoutExpired) as refused:
                        reobserve()
                self.assertEqual(refused.exception.cmd, 'read-only Git inventory')
                self.assertIs(observed_budgets[-1], budget)
                self.assertEqual(budget['deadline'], deadline)
                self.assertEqual(history, original_history)
            self.assertFalse(owner['active'])
            self.assertIsNot(review_storage._MACHINE_ACQUISITION.get(), owner)
            with self.assertRaises(state.StateError):
                reobserve()
            self.assertIs(observed_budgets[-1], budget)
            self.assertEqual(budget['deadline'], deadline)
        self.assertEqual(self.source_snapshot(context, authority_path, original_history), before)
        with review_storage._capture_birth_history(record) as (fresh_history, _):
            self.assertIsNot(review_storage._MACHINE_ACQUISITION.get(), owner)
            self.assertEqual(fresh_history, original_history)
        self.assertEqual(self.source_snapshot(context, authority_path, original_history), before)
