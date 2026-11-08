"""Presentation changes cannot alter authoritative task status or identity."""
import unittest
from runtime import title_sync


class TitleSyncTests(unittest.TestCase):
    def test_new_labels_support_topic_edits_and_pause_on_identity_change(self):
        for member in ('COORD', 'STREAM', 'AGENT'):
            with self.subTest(member=member):
                applied = title_sync.format_title('W42', member, 'Original', 'WORKING', 2)
                self.assertEqual(applied, f'\uFFFB▰▰▱▱▱ [W42] {member} · Original')
                saved = self.saved() | dict(family='W42', member=member, topic='Original', applied=applied)
                edited, desired = title_sync.reconcile(saved, applied.replace('Original', 'Chosen'),
                                                       self.task('REVIEW') | dict(user_attention=True))
                self.assertFalse(edited['paused'])
                self.assertEqual(edited['topic'], 'Chosen')
                self.assertEqual(desired, f'\uFFFB▰▰▱▱▱ REVIEW · [W42] {member} · Chosen')
                changed, desired = title_sync.reconcile(saved, applied.replace(member, 'P'), self.task())
                self.assertTrue(changed['paused'])
                self.assertIsNone(desired)

    def task(self, status='WORKING'):
        return dict(state=status, revision=3)

    def saved(self):
        title=title_sync.format_title('W1','P','Thread naming','WORKING',2)
        return dict(family='W1', member='P', topic='Thread naming', cells=2,
            applied=title, pending=None, paused=False, notified=False)

    def test_only_user_action_shows_review_waiting_or_blocked(self):
        for status in ('REVIEW', 'WAITING', 'BLOCKED'):
            with self.subTest(status=status):
                self.assertEqual(title_sync.format_title('W1', 'P', 'Topic', status, 2),
                    '\uFFFB▰▰▱▱▱ [W1] P · Topic')
                self.assertEqual(title_sync.format_title('W1', 'P', 'Topic', status, 2, user_attention=True),
                    f'\uFFFB▰▰▱▱▱ {status} · [W1] P · Topic')

    def test_formatter_keeps_meter_without_routine_labels(self):
        self.assertEqual(title_sync.format_title('W1','P','Thread naming','WORKING',0),
            '\uFFFB▱▱▱▱▱ [W1] P · Thread naming')
        self.assertEqual(title_sync.format_title('W1','P','Thread naming','WORKING',1),
            '\uFFFB▰▱▱▱▱ [W1] P · Thread naming')
        self.assertEqual(title_sync.format_title('W1','P','Thread naming','WORKING',2),
            '\uFFFB▰▰▱▱▱ [W1] P · Thread naming')
        self.assertEqual(title_sync.format_title('W1','C','Review','DONE',0), '\uFFFB▰▰▰▰▰ [W1] C · Review')
        self.assertEqual(title_sync.format_title('W1','P','Topic','REVIEW',5), '\uFFFB▰▰▰▰▱ [W1] P · Topic')
        for status in ('active','IN PROGRESS','ready'):
            with self.assertRaises(ValueError): title_sync.format_title('W1','P','Topic',status,0)

    def test_child_role_is_literal_and_numbered_members_are_rejected(self):
        self.assertEqual(title_sync.format_title('W1', 'C', 'Owner', 'WORKING', 2),
            '\uFFFB▰▰▱▱▱ [W1] C · Owner')
        for member in ('', 'C1', 'C2', 'C0', 'C01', 'C-1', 'Cx', None, 'C' + '1' * 5000):
            with self.subTest(member=member), self.assertRaises(ValueError):
                title_sync.format_title('W1', member, 'Owner', 'WORKING', 2)

    def test_title_length_bound_still_reserves_future_statuses(self):
        family = 'W' + '1' * 4067
        self.assertEqual(len(title_sync.format_title(family, 'C', 'Title', 'WAITING', 0,
            user_attention=True)), 4097)
        with self.assertRaises(ValueError):
            title_sync.format_title(family + '1', 'C', 'Title', 'TRIAGE', 0)

    def test_child_topics_and_pending_acknowledgments_preserve_role(self):
        applied = '\uFFFB▰▰▱▱▱ [W1] C · Thread naming'
        for pending in (None, '\uFFFB▰▰▱▱▱ REVIEW · [W1] C · Thread naming'):
            for topic in ('Thread naming', 'Owner topic'):
                with self.subTest(pending=pending, topic=topic):
                    saved = self.saved() | dict(member='C', applied=applied, pending=pending)
                    current = (pending or applied).replace('Thread naming', topic)
                    task = self.task('REVIEW' if pending else 'WORKING') | dict(user_attention=bool(pending))
                    updated, desired = title_sync.reconcile(saved, current, task)
                    self.assertFalse(updated['paused'])
                    self.assertIsNone(updated['pending'])
                    self.assertIsNone(desired)
                    self.assertEqual(updated['member'], 'C')
                    self.assertEqual(updated['topic'], topic)

    def test_manual_child_role_or_managed_field_change_pauses(self):
        title = '\uFFFB▰▰▱▱▱ [W1] C · Thread naming'
        for current in (title.replace(' C ', ' P '), title.replace(' C ', ' C1 '),
                        title.replace('W1', 'W2'), title.replace('▰▰▱▱▱', '▰▰▰▱▱'),
                        title.replace(' [', ' REVIEW · ['), title.removeprefix('\uFFFB'),
                        '▰▰▱▱▱ [W1] C · Thread naming · WORKING'):
            with self.subTest(current=current):
                updated, desired = title_sync.reconcile(self.saved() | dict(member='C', applied=title),
                    current, self.task())
                self.assertTrue(updated['paused'])
                self.assertIsNone(desired)

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
        self.assertEqual(desired, '\uFFFB▰▰▱▱▱ REVIEW · [W1] P · Better naming')
        self.assertFalse(new['paused'])

    def test_managed_field_or_custom_title_pauses_without_mutating_task(self):
        for current in ('My title', self.saved()['applied'].replace(' [', ' REVIEW · ['),
                        self.saved()['applied'].replace('W1','W2'),
                        self.saved()['applied'].replace(' P ',' C '),
                        self.saved()['applied'].replace('▰▰▱▱▱','▰▰▰▱▱'),
                        self.saved()['applied'].removeprefix('\uFFFB')):
            task=self.task()
            new, desired=title_sync.reconcile(self.saved(),current,task)
            self.assertTrue(new['paused'])
            self.assertIsNone(desired)
            self.assertEqual(task,self.task())

    def test_lost_acknowledgment_is_not_mistaken_for_manual_edit(self):
        saved=self.saved()
        saved['pending']=title_sync.format_title('W1','P','Thread naming','REVIEW',2,user_attention=True)
        new, desired=title_sync.reconcile(saved,saved['pending'],self.task('REVIEW') | dict(user_attention=True))
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

    def test_legacy_observations_keep_topic_edits_and_identity_during_update(self):
        for legacy in ('▰▰▱▱▱ [W1] P · Thread naming · WORKING',
                       '▰▰▱▱▱ WORKING · [W1] P · Thread naming',
                       '\uFFFB▰▰▱▱▱ WORKING · [W1] P · Thread naming'):
            for topic in ('Thread naming', 'Owner topic'):
                with self.subTest(legacy=legacy, topic=topic):
                    saved = self.saved() | dict(applied=legacy)
                    current = legacy.replace('Thread naming', topic)
                    updated, desired = title_sync.reconcile(saved, current, self.task('REVIEW'))
                    self.assertFalse(updated['paused'])
                    self.assertEqual((updated['family'], updated['member']), ('W1', 'P'))
                    self.assertEqual(updated['topic'], topic)
                    self.assertEqual(desired, f'\uFFFB▰▰▱▱▱ [W1] P · {topic}')

    def test_manual_layout_change_pauses_in_both_directions(self):
        legacy = '▰▰▱▱▱ [W1] P · Thread naming · WORKING'
        current = '▰▰▱▱▱ WORKING · [W1] P · Thread naming'
        for before, after in ((legacy, current), (current, legacy)):
            with self.subTest(before=before):
                updated, desired = title_sync.reconcile(self.saved() | dict(applied=before), after, self.task())
                self.assertTrue(updated['paused'])
                self.assertIsNone(desired)

    def test_legacy_pending_acknowledgment_and_owner_topic_are_reconciled(self):
        legacy = '▰▰▱▱▱ [W1] P · Thread naming · REVIEW'
        for topic in ('Thread naming', 'Owner topic'):
            with self.subTest(topic=topic):
                updated, desired = title_sync.reconcile(self.saved() | dict(pending=legacy),
                    legacy.replace('Thread naming', topic), self.task('REVIEW'))
                self.assertFalse(updated['paused'])
                self.assertIsNone(updated['pending'])
                self.assertEqual(desired, f'\uFFFB▰▰▱▱▱ [W1] P · {topic}')

    def test_manual_removal_of_attention_label_pauses_sync(self):
        applied = title_sync.format_title('W1', 'P', 'Thread naming', 'WAITING', 2, user_attention=True)
        current = self.saved()['applied']
        updated, desired = title_sync.reconcile(self.saved() | dict(applied=applied), current,
                                                self.task('WAITING') | dict(user_attention=True))
        self.assertTrue(updated['paused'])
        self.assertIsNone(desired)
