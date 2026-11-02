"""Presentation changes cannot alter authoritative task status or identity."""
import unittest
from runtime import title_sync


class TitleSyncTests(unittest.TestCase):
    def task(self, status='WORKING'):
        return dict(state=status, revision=3)

    def saved(self):
        title=title_sync.format_title('W1','P','Thread naming','WORKING',2)
        return dict(family='W1', member='P', topic='Thread naming', cells=2,
            applied=title, pending=None, paused=False, notified=False)

    def test_formatter_uses_exact_status_and_full_meter_only_for_done(self):
        self.assertEqual(title_sync.format_title('W1','P','Thread naming','WORKING',2),
            '▰▰▱▱▱ [W1] P · Thread naming · WORKING')
        self.assertTrue(title_sync.format_title('W1','C2','Review','DONE',0).startswith('▰▰▰▰▰ '))
        self.assertTrue(title_sync.format_title('W1','P','Topic','REVIEW',5).startswith('▰▰▰▰▱ '))
        for status in ('active','IN PROGRESS','ready'):
            with self.assertRaises(ValueError): title_sync.format_title('W1','P','Topic',status,0)

    def test_topic_edit_adopted_while_new_status_applied(self):
        saved=self.saved()
        current=saved['applied'].replace('Thread naming','Better naming')
        new, desired=title_sync.reconcile(saved,current,self.task('REVIEW'))
        self.assertEqual(new['topic'],'Better naming')
        self.assertTrue(desired.endswith('Better naming · REVIEW'))
        self.assertFalse(new['paused'])

    def test_managed_field_or_custom_title_pauses_without_mutating_task(self):
        for current in ('My title', self.saved()['applied'].replace('WORKING','DONE'),
                        self.saved()['applied'].replace('W1','W2'),
                        self.saved()['applied'].replace(' P ',' C1 '),
                        self.saved()['applied'].replace('▰▰▱▱▱','▰▰▰▱▱')):
            task=self.task()
            new, desired=title_sync.reconcile(self.saved(),current,task)
            self.assertTrue(new['paused'])
            self.assertIsNone(desired)
            self.assertEqual(task,self.task())

    def test_lost_acknowledgment_is_not_mistaken_for_manual_edit(self):
        saved=self.saved()
        saved['pending']=title_sync.format_title('W1','P','Thread naming','REVIEW',2)
        new, desired=title_sync.reconcile(saved,saved['pending'],self.task('REVIEW'))
        self.assertFalse(new['paused'])
        self.assertIsNone(desired)
        self.assertIsNone(new['pending'])

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
