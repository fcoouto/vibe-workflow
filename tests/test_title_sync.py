"""Presentation changes cannot alter authoritative task status or identity."""
import unittest
from runtime import title_sync


class TitleSyncTests(unittest.TestCase):
    def task(self, status='WORKING'):
        return dict(state=status, revision=3)

    def saved(self):
        title=title_sync.format_title('Thread naming','WORKING',2)
        return dict(topic='Thread naming', cells=2,
            applied=title, pending=None, paused=False, notified=False)

    def test_format_has_no_family_or_member(self):
        self.assertEqual(title_sync.format_title('Thread naming', 'WORKING', 1), '\uFFFB▰▱▱▱▱ Thread naming')
        self.assertEqual(title_sync.format_title('Thread naming', 'REVIEW', 4, user_attention=True),
                         '\uFFFB▰▰▰▰▱ REVIEW · Thread naming')
        self.assertEqual(title_sync.format_title('Review', 'DONE', 0), '\uFFFB▰▰▰▰▰ Review')
        with self.assertRaises(ValueError):
            title_sync.format_title('REVIEW · Topic', 'WORKING', 1)

    def test_other_shapes_are_not_managed(self):
        for title in ('\uFFFB▰▰▱▱▱ [W1] P · Topic', '\uFFFB▰▰▱▱▱ REVIEW · [W1] C · X',
                      '▰▰▱▱▱ [W1] P · Topic · WORKING', '▰▰▱▱▱ Topic'):
            with self.subTest(title=title):
                self.assertIsNone(title_sync._parsed(title))

    def test_other_shape_current_title_pauses(self):
        saved = self.saved() | dict(applied='\uFFFB▰▰▱▱▱ Thread naming')
        updated, desired = title_sync.reconcile(saved, '\uFFFB▰▰▱▱▱ [W1] P · Thread naming', self.task())
        self.assertTrue(updated['paused'])
        self.assertIsNone(desired)

    def test_retired_statuses_are_never_rendered(self):
        for status in ('READY', 'BLOCKED'):
            with self.subTest(status=status), self.assertRaises(ValueError):
                title_sync.format_title('Topic', status, 2, user_attention=True)

    def test_only_user_action_shows_review_or_waiting(self):
        for status in ('REVIEW', 'WAITING'):
            with self.subTest(status=status):
                self.assertEqual(title_sync.format_title('Topic', status, 2), '\uFFFB▰▰▱▱▱ Topic')
                self.assertEqual(title_sync.format_title('Topic', status, 2, user_attention=True),
                    f'\uFFFB▰▰▱▱▱ {status} · Topic')

    def test_formatter_keeps_meter_without_routine_labels(self):
        self.assertEqual(title_sync.format_title('Thread naming','WORKING',0), '\uFFFB▱▱▱▱▱ Thread naming')
        self.assertEqual(title_sync.format_title('Thread naming','WORKING',2), '\uFFFB▰▰▱▱▱ Thread naming')
        self.assertEqual(title_sync.format_title('Topic','REVIEW',5), '\uFFFB▰▰▰▰▱ Topic')
        for status in ('active','IN PROGRESS','ready'):
            with self.assertRaises(ValueError): title_sync.format_title('Topic',status,0)

    def test_working_fallback_applies_only_without_measurable_milestones(self):
        self.assertEqual(title_sync.progress_cells(self.task()), 1)
        self.assertEqual(title_sync.progress_cells(self.task() | dict(milestones=[])), 1)
        self.assertEqual(title_sync.progress_cells(self.task('REVIEW')), 0)
        milestones = [dict(weight=1, accepted=True), dict(weight=5, accepted=False)]
        self.assertEqual(title_sync.progress_cells(self.task() | dict(milestones=milestones)), 0)

    def test_topic_edit_adopted_while_new_status_applied(self):
        saved=self.saved()
        current=saved['applied'].replace('Thread naming','Better naming')
        new, desired=title_sync.reconcile(saved,current,self.task('REVIEW') | dict(user_attention=True))
        self.assertEqual(new['topic'],'Better naming')
        self.assertEqual(desired, '\uFFFB▰▰▱▱▱ REVIEW · Better naming')
        self.assertFalse(new['paused'])

    def test_managed_field_or_custom_title_pauses_without_mutating_task(self):
        applied = self.saved()['applied']
        for current in ('My title', applied.replace('▰▰▱▱▱ ', '▰▰▱▱▱ REVIEW · '),
                        applied.replace('▰▰▱▱▱','▰▰▰▱▱'), applied.removeprefix('\uFFFB')):
            with self.subTest(current=current):
                task=self.task()
                new, desired=title_sync.reconcile(self.saved(),current,task)
                self.assertTrue(new['paused'])
                self.assertIsNone(desired)
                self.assertEqual(task,self.task())

    def test_lost_acknowledgment_is_not_mistaken_for_manual_edit(self):
        saved=self.saved()
        saved['pending']=title_sync.format_title('Thread naming','REVIEW',2,user_attention=True)
        new, desired=title_sync.reconcile(saved,saved['pending'],self.task('REVIEW') | dict(user_attention=True))
        self.assertFalse(new['paused'])
        self.assertIsNone(desired)
        self.assertIsNone(new['pending'])

    def test_topic_edit_of_pending_acknowledgment_is_adopted(self):
        pending = title_sync.format_title('Thread naming', 'REVIEW', 2, user_attention=True)
        updated, desired = title_sync.reconcile(self.saved() | dict(pending=pending),
            pending.replace('Thread naming', 'Owner topic'), self.task('REVIEW') | dict(user_attention=True))
        self.assertFalse(updated['paused'])
        self.assertIsNone(updated['pending'])
        self.assertEqual(updated['topic'], 'Owner topic')
        self.assertIsNone(desired)

    def test_paused_sync_stays_paused_without_explicit_resume(self):
        saved=self.saved()|dict(paused=True)
        new, desired=title_sync.reconcile(saved,saved['applied'],self.task('DONE'))
        self.assertTrue(new['paused'])
        self.assertIsNone(desired)

    def test_unsupported_topic_is_preserved_as_manual_override(self):
        saved = self.saved()
        current = saved['applied'].replace('Thread naming', 'x' * 161)
        updated, desired = title_sync.reconcile(saved, current, self.task('REVIEW'))
        self.assertTrue(updated['paused'])
        self.assertIsNone(desired)

    def test_manual_removal_of_attention_label_pauses_sync(self):
        applied = title_sync.format_title('Thread naming', 'WAITING', 2, user_attention=True)
        current = self.saved()['applied']
        updated, desired = title_sync.reconcile(self.saved() | dict(applied=applied), current,
                                                self.task('WAITING') | dict(user_attention=True))
        self.assertTrue(updated['paused'])
        self.assertIsNone(desired)
