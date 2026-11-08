"""Disposable D4 transactions exercise ownership, durability and recovery."""
import copy
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import unittest
from unittest.mock import patch
from tests import test_onboarding as d3_tests
from runtime import onboarding
from runtime.startup import PROJECT_FILE, policy_digest
from tests.fixture_changes import ChangesFixture, disable_inherited_d3_tests


class ChangesLegacyTests(ChangesFixture, d3_tests.OnboardingTests):

    def test_linked_foreign_check_uses_relevant_ledger_claims_and_corrupt_links_block(self):
        from tests.test_evidence import EvidenceTests
        from runtime import evidence, state
        from runtime.startup import ProjectScope, StartupError
        fixture = EvidenceTests(methodName='runTest')
        fixture._canonical_peer=True
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.plan()
        definition = evidence._load_plan(fixture.scope, fixture.identity)
        definition['checks'][0]['resources'] = [dict(kind='service', key='fixture:unrelated', host='local', mode='read')]
        evidence.plan_evidence(fixture.scope, fixture.request(plan=definition, change_decision_ref='user:declared-resource'))
        checked = evidence.run_check(fixture.scope, fixture.request(check_id='unit'))
        target = fixture.peer_scope
        self.changes._writers({}, {}, {'task_reconciliation': True}, scope=target)
        with state._database(fixture.scope) as db:
            original = state._task(db, state._scope_id(fixture.scope), 'codex', 'parent')
        for delta in ('missing-link', 'wrong-claims', 'wrong-definition'):
            corrupt = copy.deepcopy(original)
            attempt = corrupt['evidence']['attempts'][0]
            if delta == 'missing-link':
                attempt.pop('ledger_assignment_digest')
            elif delta == 'wrong-claims':
                attempt['ledger_claims_digest'] = 'f' * 64
            else:
                attempt['check_id'] = 'another-check'
            with state._database(fixture.scope, write=True) as db:
                state._save(db, state._scope_id(fixture.scope), corrupt)
            with self.subTest(delta=delta), self.assertRaises(StartupError):
                self.changes._writers({}, {}, {'task_reconciliation': True}, scope=target)
        with state._database(fixture.scope, write=True) as db:
            state._save(db, state._scope_id(fixture.scope), original)
        self.assertEqual(checked['attempt']['status'], 'uncertain')

    def test_linked_foreign_review_requires_exact_plain_prompt_and_pair_before_independence(self):
        from tests.test_delegation import Stage4NativeReviewTests
        from runtime import state
        from runtime.startup import ProjectScope, StartupError
        fixture = Stage4NativeReviewTests(methodName='runTest')
        fixture._canonical_peer=True
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        plan = fixture.review_plan()
        plan['jobs'][0]['claims'] = [dict(kind='service', key='fixture:independent-review', host='local', mode='read')]
        fixture.plan(plan)
        prepared = fixture.prepared(assignment='Review only the declared independent service read-only.')
        target = fixture.peer_scope
        self.changes._writers({}, {}, {'task_reconciliation': True}, scope=target)
        with state._database(fixture.scope) as db:
            original = state._task(db, state._scope_id(fixture.scope), 'codex', 'parent')
        for change in ('missing-link', 'plain-digest', 'wrong-job'):
            corrupt = copy.deepcopy(original)
            attempt = corrupt['evidence']['attempts'][0]
            if change == 'missing-link':
                attempt.pop('ledger_assignment_digest')
            elif change == 'plain-digest':
                attempt['assignment_digest'] = 'f' * 64
            else:
                attempt['job_id'] = 'another-review'
            with state._database(fixture.scope, write=True) as db:
                state._save(db, state._scope_id(fixture.scope), corrupt)
            with self.subTest(change=change), self.assertRaises(StartupError):
                self.changes._writers({}, {}, {'task_reconciliation': True}, scope=target)
        with state._database(fixture.scope, write=True) as db:
            state._save(db, state._scope_id(fixture.scope), original)
        self.assertEqual(prepared['attempt']['status'], 'reserved')

    def test_legacy_linked_unresolved_owner_blocks_before_enrollment_and_configuration_mutations(self):
        self.assert_legacy_foreign_owner_blocks_before_mutation()

    def test_legacy_linked_active_editable_owner_without_check_blocks_before_mutations(self):
        self.assert_legacy_foreign_owner_blocks_before_mutation(unresolved=False, task_state='active')

    def test_legacy_unrelated_readonly_unresolved_attempt_has_unknown_shared_footprint(self):
        self.assert_legacy_foreign_owner_blocks_before_mutation(linked=False, mode='read-only')

    def test_legacy_linked_retirement_must_match_before_incompatible_activation(self):
        record, _, request, database, task = self.legacy_other_checkout(retired=True)
        observation = self.observe(request)['observations']
        before = {str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}
        self.changes._writers(self.plan(), record, observation)
        self.assertEqual({str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}, before)
        self.rewrite_legacy_task(database, task | dict(turn_id='another-turn'))
        corrupt_before = {str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}
        with self.assertRaises(self.changes.Pending):
            self.changes._writers(self.plan(), record, observation)
        self.assertEqual({str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}, corrupt_before)
        self.rewrite_legacy_task(database, task)
        before = {str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}
        result = self.complete(request)
        if result['exit_code'] == 2:
            result = self.changes.resume_plan(self.directory, self.observe(request))
        self.assert_activation_incompatible(result)
        self.assertFalse((self.base / 'fresh-state').exists())
        self.assertEqual({str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}, before)
        self.assertEqual(task['pins']['schema_version'], 1)

    def test_legacy_linked_readonly_owner_without_attempts_does_not_claim_mutation(self):
        record, _, request, database, _ = self.legacy_other_checkout(unresolved=False, mode='read-only', task_state='active')
        before = {str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}
        self.changes._writers(self.plan(), record, self.observe(request)['observations'])
        self.assertEqual({str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}, before)

    def test_legacy_unrelated_retired_owner_remains_safely_reconciled(self):
        record, _, request, database, _ = self.legacy_other_checkout(linked=False, retired=True)
        before = {str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}
        self.changes._writers(self.plan(), record, self.observe(request)['observations'])
        self.assertEqual({str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}, before)

    def test_legacy_retired_state_preserves_history_and_refuses_current_activation(self):
        record, _, request, database, task = self.legacy_transition(unresolved=True)
        before = {str(p): p.read_bytes() for p in database.parent.rglob("*") if p.is_file()}
        result = self.complete(request)
        if result["exit_code"] == 2:
            result = self.changes.resume_plan(self.directory, self.observe(request))
        self.assert_activation_incompatible(result)
        self.assertFalse((self.base / "fresh-state").exists())
        self.assertEqual({str(p): p.read_bytes() for p in database.parent.rglob("*") if p.is_file()}, before)
        self.assertEqual(task["pins"]["schema_version"], 1)
        from runtime import startup
        with self.assertRaises(startup.StartupError):
            startup.runtime_settings(record, onboarding.PACKAGE, Path(record["git_common_dir"]))

    def test_legacy_cancelled_flag_without_retained_proof_cannot_release_writer(self):
        _, original, request, database, _ = self.legacy_transition(retired=False, unresolved=True)
        before = database.read_bytes()
        result = self.changes.apply_plan(self.directory, self.observe(request))
        self.assertEqual(result["exit_code"], 2, result)
        self.assertEqual((self.repo / PROJECT_FILE).read_bytes(), original)
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nlegacy process\n")
        self.assertEqual(database.read_bytes(), before)

    def test_legacy_proof_binding_corruption_and_unsafe_state_block_transition(self):
        record, _, request, database, task = self.legacy_transition(unresolved=True)
        observation = self.observe(request)["observations"]
        self.changes._writers(self.plan(), record, observation)
        changes = [dict(pins=task["pins"] | dict(schema_version=2)), dict(turn_id="different"),
            dict(revision=3), dict(retirement=task["retirement"] | dict(observation_ref="unbound")),
            dict(evidence=task["evidence"] | dict(attempts=[]))]
        for delta in changes:
            with self.subTest(delta=delta):
                self.rewrite_legacy_task(database, task | delta)
                with self.assertRaises(self.changes.Pending):
                    self.changes._writers(self.plan(), record, observation)
        self.rewrite_legacy_task(database, task)
        artifact = database.parent / "evidence" / task["retirement"]["proof"]["name"]
        raw = artifact.read_bytes()
        proof = json.loads(raw)
        for delta in (dict(expected_revision=True), dict(expected_pins=task["pins"] | dict(schema_version=True))):
            with self.subTest(proof=delta):
                altered = json.dumps(proof | delta).encode()
                artifact.chmod(0o600)
                artifact.write_bytes(altered)
                artifact.chmod(0o400)
                retirement = task["retirement"] | dict(proof=dict(name=artifact.name,
                    digest=hashlib.sha256(altered).hexdigest()))
                self.rewrite_legacy_task(database, task | dict(retirement=retirement))
                with self.assertRaises(self.changes.Pending):
                    self.changes._writers(self.plan(), record, observation)
        artifact.chmod(0o600)
        artifact.write_bytes(raw)
        artifact.chmod(0o400)
        self.rewrite_legacy_task(database, task)
        artifact.chmod(0o600)
        with self.assertRaises(self.changes.Pending):
            self.changes._writers(self.plan(), record, observation)
        artifact.chmod(0o400)
        database.chmod(0o644)
        with self.assertRaises(self.changes.Pending):
            self.changes._writers(self.plan(), record, observation)
        database.chmod(0o600)
        retained = database.with_suffix(".retained")
        database.rename(retained)
        with self.assertRaises(self.changes.Pending):
            self.changes._writers(self.plan(), record, observation)
        self.assertFalse(database.exists())
        retained.rename(database)
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("create view unsupported as select * from tasks")
        with self.assertRaises(self.changes.Pending):
            self.changes._writers(self.plan(), record, observation)
        self.assertEqual(artifact.read_bytes(), raw)

    def test_legacy_schema_one_rejects_shape_valid_schema_two_delegation_state(self):
        record, _, request, database, task = self.legacy_transition()
        task['delegation'] = dict(version=1, plan=task['retirement']['proof'],
                                  attempts_consumed=0, resource_units_consumed=0)
        self.rewrite_legacy_task(database, task)
        before = {str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}
        with self.assertRaises(self.changes.Pending):
            self.changes._writers(self.plan(), record, self.observe(request)['observations'])
        self.assertEqual({str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}, before)

    def test_legacy_null_turn_checkout_blocks_without_mutating_history(self):
        self.assert_legacy_null_event_scope_is_rejected("turns")

    def test_legacy_null_receipt_checkout_blocks_without_mutating_history(self):
        self.assert_legacy_null_event_scope_is_rejected("receipts")

    def test_legacy_recovery_inspects_backup_and_blocks_new_unreconciled_writer(self):
        _, original, request, database, task = self.legacy_transition()
        fired = False
        def crash(stage):
            nonlocal fired
            if stage == "rename" and not fired:
                fired = True
                raise RuntimeError("interrupted transition")
        with patch.object(self.changes, "_checkpoint", side_effect=crash):
            with self.assertRaises(RuntimeError):
                self.changes.apply_plan(self.directory, self.observe(request))
        self.assertFalse((self.repo / PROJECT_FILE).exists())
        active = dict(task, state="active")
        active.pop("retirement")
        self.rewrite_legacy_task(database, active)
        result = self.changes.resume_plan(self.directory, self.observe(request))
        self.assertEqual(result["exit_code"], 2, result)
        self.assertFalse((self.repo / PROJECT_FILE).exists())
        self.rewrite_legacy_task(database, task)
        result = self.changes.resume_plan(self.directory, self.observe(request))
        if result["exit_code"] == 2:
            result = self.changes.resume_plan(self.directory, self.observe(request))
        self.assert_activation_incompatible(result)
        result = self.changes.rollback_plan(self.directory, self.observe(request))
        self.assertEqual(result["exit_code"], 2, result)
        self.assertFalse((self.repo / PROJECT_FILE).exists())
        self.assertEqual((self.directory / "enrollment.backup").read_bytes(), original)
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nlegacy process\n")


disable_inherited_d3_tests(ChangesLegacyTests)
