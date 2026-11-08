"""Private guidance-cache fixtures; no network, models or live client changes."""
from tests.fixture_canonical_birth import fixture_artifact
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import importlib
import json
import hashlib
import sys
import os
import sqlite3
import shutil
import subprocess
from contextlib import closing
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from runtime import changes, delegation, evidence, state
from tests import test_evidence
from runtime.startup import ProjectScope, RuntimeSettings


class GuidanceTests(unittest.TestCase):
    def setUp(self):
        from tests.fixture_canonical_birth import fresh_canonical
        self._canonical_peer=self._testMethodName=='test_shared_projects_and_concurrent_refresh_have_only_one_holder'
        fixture, self.acquisition, _ = fresh_canonical(self)
        if self._canonical_peer:
            self.peer_scope=next(a.resolution.scope for root,a in fixture.member_acquisitions.items() if root!=str(fixture.repo))
        self.base, self.scope = fixture.base, self.acquisition.resolution.scope
        self.request = dict(client_surface="codex-desktop", applicability=dict(
            client_version="1.0", capability_fingerprint="a" * 64))
        self.guidance = dict(schema_version=1, client_surface="codex-desktop", checked_at="2026-01-01T00:00:00Z",
            applicability=deepcopy(self.request["applicability"]),
            sources=[dict(id="official", url="https://example.org/official/models", sha256="b" * 64)],
            recommendations=[dict(id="small", work="Small bounded edits", risk="Low", models=[dict(
                model="native-model", efforts=["low", "medium"])], conditions=["Supported controls available"],
                limitations=["No measured pricing claim"], source_ids=["official"])])

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("runtime.delegation"), "guidance cache implementation missing")
        return importlib.import_module("runtime.delegation")

    def status(self, **updates):
        return self.api().guidance_status(self.scope, self.request | updates)

    def claim(self, kind="routine", trigger_id="task", **updates):
        return self.api().claim_guidance(self.scope, self.request | dict(
            trigger=dict(kind=kind, id=trigger_id)) | updates)

    def record(self, claim, **updates):
        return self.api().record_guidance(self.scope, self.request | dict(token=claim["token"],
            generation=claim["generation"], coordinator_ref="coordinator:verified", guidance=self.guidance) | updates)

    def publish(self):
        return self.record(self.claim())

    def cache_path(self):
        return self.scope.runtime.state_dir / "model-guidance/codex-desktop.json"

    def test_first_use_publication_and_compatible_tasks_need_no_refresh(self):
        self.assertEqual(self.status()["freshness"], "unavailable")
        claim = self.claim()
        self.assertTrue(claim["claimed"])
        result = self.record(claim)
        self.assertEqual(result["freshness"], "verified")
        self.assertTrue(result["usable"])
        self.assertFalse(result["refresh_needed"])
        self.assertEqual(self.status()["recommendations"], [])
        selected = self.status(profile_ids=["small"])
        self.assertEqual(selected["recommendations"], self.guidance["recommendations"])
        self.assertEqual(self.claim()["claimed"], False)
        self.assertEqual(self.claim(trigger_id="next-task")["claimed"], False)
        reference = result["snapshot_ref"]
        from runtime import evidence
        self.assertEqual(evidence._read(self.scope, reference), self.guidance)
        self.assertEqual(reference["digest"], result["snapshot_digest"])
        self.assertEqual(len(result["snapshot_digest"]), 64)
        self.assertTrue((self.scope.runtime.state_dir/"runtime.sqlite3").is_file())
        with state._database(self.scope) as db:self.assertEqual(db.execute("select count(*) from tasks").fetchone()[0],0)

    def test_failure_preserves_last_good_bytes_time_and_suppresses_routine_storms(self):
        original = self.publish()
        raw = self.cache_path().read_bytes()
        claim = self.claim("maintenance", "run-1")
        failure = self.request | dict(token=claim["token"], generation=claim["generation"],
            coordinator_ref="coordinator:offline", failure="Official source unavailable")
        result = self.api().record_guidance(self.scope, failure)
        self.assertEqual(result["freshness"], "stale")
        self.assertTrue(result["usable"])
        self.assertEqual(result["checked_at"], original["checked_at"])
        self.assertEqual(self.cache_path().read_bytes(), raw)
        self.assertFalse(self.claim()["claimed"])
        self.assertFalse(self.claim("maintenance", "run-1")["claimed"])
        self.assertTrue(self.claim("maintenance", "run-2")["claimed"])

    def test_successful_manual_trigger_replay_does_not_admit_another_refresh(self):
        self.publish()
        claim = self.claim("manual", "retry-1")
        self.record(claim)
        self.assertFalse(self.claim("manual", "retry-1")["claimed"])
        self.assertTrue(self.claim("manual", "retry-2")["claimed"])

    def test_duplicate_fields_do_not_establish_cache_or_control_validity(self):
        self.publish()
        original = self.cache_path().read_text()
        self.cache_path().write_text('{"guidance":{},' + original[1:])
        self.assertEqual(self.status()["freshness"], "unavailable")
        self.cache_path().write_text(original)
        control = self.cache_path().with_name("codex-desktop.control.json")
        original_control = control.read_text()
        control.write_text('{"generation":0,' + original_control[1:])
        with self.assertRaises(state.StateError):
            self.claim()

    def test_interruption_after_snapshot_publication_keeps_valid_snapshot_and_blocks_old_token(self):
        api = self.api()
        original_atomic = api._atomic
        def fail_control(path, value, **kwargs):
            if path.name.endswith(".control.json"):
                raise OSError("lost control publication")
            original_atomic(path, value, **kwargs)
        with patch.object(api, "_now", return_value=1800000000):
            claim = self.claim(lease_seconds=1)
            with patch.object(api, "_atomic", side_effect=fail_control):
                with self.assertRaises(state.StateError):
                    self.record(claim)
            self.assertTrue(self.status()["usable"])
        with patch.object(api, "_now", return_value=1800000002):
            with self.assertRaises(state.StateError):
                self.record(claim)
            replacement = self.claim()
            self.assertTrue(replacement["claimed"])
            self.assertTrue(self.record(replacement)["usable"])

    def test_failed_missing_refresh_suppresses_first_use_until_explicit_retry(self):
        claim = self.claim()
        failure = self.request | dict(token=claim["token"], generation=claim["generation"],
            coordinator_ref="coordinator:offline", failure="Offline")
        self.api().record_guidance(self.scope, failure)
        for number in range(4):
            self.assertFalse(self.claim(trigger_id=str(number))["claimed"])
        self.assertEqual(self.status()["freshness"], "unavailable")
        self.assertTrue(self.claim("manual", "retry-1")["claimed"])

    def test_relevant_changes_block_reuse_and_allow_new_refresh_after_failure(self):
        self.publish()
        claim = self.claim("manual", "retry")
        self.api().record_guidance(self.scope, self.request | dict(token=claim["token"],
            generation=claim["generation"], coordinator_ref="coordinator:offline", failure="Offline"))
        changed = dict(client_version="2.0", capability_fingerprint="c" * 64)
        self.assertEqual(self.status(applicability=changed)["freshness"], "incompatible")
        self.assertFalse(self.status(applicability=changed)["usable"])
        self.assertTrue(self.claim(applicability=changed)["claimed"])

    def test_corrupt_cache_is_unavailable_and_new_corruption_is_a_refresh_trigger(self):
        self.publish()
        self.cache_path().write_text("invalid")
        self.assertEqual(self.status()["freshness"], "unavailable")
        claim = self.claim()
        self.assertTrue(claim["claimed"])
        self.api().record_guidance(self.scope, self.request | dict(token=claim["token"],
            generation=claim["generation"], coordinator_ref="coordinator:offline", failure="Offline"))
        self.assertFalse(self.claim()["claimed"])
        self.cache_path().write_text("different-invalid")
        self.assertTrue(self.claim()["claimed"])

    def test_shared_projects_and_concurrent_refresh_have_only_one_holder(self):
        api = self.api()
        barrier = threading.Barrier(15)
        other = self.peer_scope
        def run(number):
            barrier.wait(timeout=5)
            return api.claim_guidance(other if number % 2 else self.scope, self.request | dict(
                trigger=dict(kind="routine", id=str(number))))
        with ThreadPoolExecutor(max_workers=15) as executor:
            results = list(executor.map(run, range(15)))
        self.assertEqual(sum(result["claimed"] for result in results), 1)
        self.assertTrue(all(result["refresh_in_progress"] for result in results))
        self.assertTrue(all("token" not in result for result in results if not result["claimed"]))

    def test_expired_claim_recovers_and_late_holder_cannot_publish(self):
        api = self.api()
        with patch.object(api, "_now", return_value=1800000000):
            first = self.claim(lease_seconds=1)
        with patch.object(api, "_now", return_value=1800000002):
            with self.assertRaises(state.StateError):
                self.record(first)
            second = self.claim()
            self.assertTrue(second["claimed"])
            self.assertGreater(second["generation"], first["generation"])
            with self.assertRaises(state.StateError):
                self.record(first)
            self.assertTrue(self.record(second)["usable"])

    def test_changed_snapshot_or_applicability_rejects_outdated_publication(self):
        self.publish()
        claim = self.claim("manual", "retry")
        original = self.cache_path().read_bytes()
        self.cache_path().write_bytes(original + b" ")
        with self.assertRaises(state.StateError):
            self.record(claim)
        self.cache_path().write_bytes(original)
        changed = dict(client_version="2.0", capability_fingerprint="a" * 64)
        with self.assertRaises(state.StateError):
            self.record(claim, applicability=changed)
        self.assertTrue(self.record(claim)["usable"])
        with self.assertRaises(state.StateError):
            self.record(claim)

    def test_failed_atomic_publication_retains_complete_snapshot_and_claim_can_retry(self):
        api = self.api()
        self.publish()
        original = self.cache_path().read_bytes()
        claim = self.claim("manual", "retry")
        with patch.object(api.os, "replace", side_effect=OSError("interrupted")):
            with self.assertRaises(state.StateError):
                self.record(claim)
        self.assertEqual(self.cache_path().read_bytes(), original)
        self.assertTrue(self.record(claim)["usable"])
        self.assertFalse(list(self.cache_path().parent.glob("*.tmp")))

    def test_claim_expiring_during_snapshot_preparation_cannot_publish(self):
        api = self.api()
        from runtime import evidence
        original = evidence._artifact
        clock = [1800000000]
        def prepare(scope, value, **kwargs):
            reference = original(scope, value, **kwargs)
            clock[0] += 2
            return reference
        with patch.object(api, "_now", side_effect=lambda: clock[0]):
            claim = self.claim(lease_seconds=1)
            with patch.object(evidence, "_artifact", side_effect=prepare):
                with self.assertRaises(state.StateError):
                    self.record(claim)
        self.assertFalse(self.cache_path().exists())

    def test_deep_corrupt_json_is_bounded_and_never_establishes_guidance(self):
        self.publish()
        nested = '{"nested":' + '[' * 30000 + '0' + ']' * 30000 + '}'
        self.cache_path().write_text(nested)
        self.assertEqual(self.status()["freshness"], "unavailable")
        control = self.cache_path().with_name("codex-desktop.control.json")
        control.write_text(nested)
        with self.assertRaises(state.StateError):
            self.status()

    def test_future_checked_time_never_establishes_valid_guidance(self):
        api = self.api()
        with patch.object(api, "_now", return_value=1800000000):
            claim = self.claim()
            guidance = self.guidance | dict(checked_at="2099-01-01T00:00:00Z")
            with self.assertRaises(state.StateError):
                self.record(claim, guidance=guidance)
            self.assertFalse(self.cache_path().exists())
            self.assertTrue(self.record(claim)["usable"])

    def test_age_alone_does_not_expire_compatible_guidance(self):
        self.publish()
        with patch.object(self.api(), "_now", return_value=2100000000):
            self.assertEqual(self.status()["freshness"], "verified")
            self.assertFalse(self.claim()["claimed"])

    def test_strict_bounded_schema_and_source_references_reject_without_publication(self):
        claim = self.claim()
        invalid = []
        invalid.append(self.guidance | dict(secret="forbidden"))
        invalid.append(self.guidance | dict(schema_version=True))
        invalid.append(self.guidance | dict(client_surface="codex-cli"))
        invalid.append(self.guidance | dict(checked_at="2026-01-01T00:00:00+01:00"))
        invalid.append(self.guidance | dict(sources=[self.guidance["sources"][0] | dict(url="http://example.org")]))
        invalid.append(self.guidance | dict(sources=[self.guidance["sources"][0] | dict(sha256="not-a-digest")]))
        invalid.append(self.guidance | dict(recommendations=[self.guidance["recommendations"][0] | dict(source_ids=["missing"])]))
        invalid.append(self.guidance | dict(recommendations=[self.guidance["recommendations"][0]] * 33))
        invalid.append(self.guidance | dict(recommendations=[self.guidance["recommendations"][0] | dict(work="x" * 70000)]))
        for guidance in invalid:
            with self.subTest(guidance=str(guidance)[:80]), self.assertRaises(state.StateError):
                self.record(claim, guidance=guidance)
        self.assertFalse(self.cache_path().exists())
        self.assertTrue(self.record(claim)["usable"])

    def test_untrusted_requests_and_unsafe_paths_fail_without_following_links(self):
        api = self.api()
        for request in (self.request | dict(client_surface="../escape"), self.request | dict(secret="x"),
                        self.request | dict(applicability={"client_version": "1"})):
            with self.subTest(request=request), self.assertRaises(state.StateError):
                api.guidance_status(self.scope, request)
        self.claim()
        outside = self.base / "outside"
        outside.write_text("private")
        self.cache_path().symlink_to(outside)
        with self.assertRaises(state.StateError):
            self.status()
        self.assertEqual(outside.read_text(), "private")
        self.cache_path().unlink()
        self.cache_path().write_text("{}"); self.cache_path().chmod(0o644)
        with self.assertRaises(state.StateError):
            self.status()

    def assert_artifact_loss_admits_one_recovery(self, corrupt=False):
        published = self.publish()
        cache_bytes = self.cache_path().read_bytes()
        failed = self.claim("maintenance", "run-before-artifact-loss")
        self.api().record_guidance(self.scope, self.request | dict(token=failed["token"],
            generation=failed["generation"], coordinator_ref="coordinator:offline", failure="Offline"))
        self.assertEqual(self.status()["freshness"], "stale")
        self.assertFalse(self.claim()["claimed"])
        artifact = self.scope.runtime.state_dir / "evidence" / published["snapshot_ref"]["name"]
        if corrupt:
            artifact.chmod(0o600)
            artifact.write_text("{}")
            artifact.chmod(0o400)
        else:
            artifact.unlink()
        status = self.status()
        self.assertEqual(status["freshness"], "unavailable")
        self.assertFalse(status["usable"])
        self.assertTrue(status["refresh_needed"])
        recovery = self.claim()
        self.assertTrue(recovery["claimed"])
        self.assertFalse(self.claim(trigger_id="concurrent-recovery")["claimed"])
        self.api().record_guidance(self.scope, self.request | dict(token=recovery["token"],
            generation=recovery["generation"], coordinator_ref="coordinator:still-offline", failure="Still offline"))
        self.assertFalse(self.status()["refresh_needed"])
        for number in range(3):
            self.assertFalse(self.claim(trigger_id="routine-" + str(number))["claimed"])
        self.assertEqual(self.cache_path().read_bytes(), cache_bytes)
        self.assertTrue(self.claim("manual", "explicit-retry")["claimed"])

    def test_missing_artifact_after_failed_refresh_admits_one_recovery_then_suppresses_failures(self):
        self.assert_artifact_loss_admits_one_recovery()

    def test_corrupt_artifact_after_failed_refresh_admits_one_recovery_then_suppresses_failures(self):
        self.assert_artifact_loss_admits_one_recovery(corrupt=True)

    def test_tampered_artifact_cannot_establish_guidance(self):
        result = self.publish()
        artifact = self.scope.runtime.state_dir / "evidence" / result["snapshot_ref"]["name"]
        artifact.chmod(0o600)
        artifact.write_text("{}")
        artifact.chmod(0o400)
        self.assertFalse(self.status()["usable"])
        self.assertTrue(self.claim()["claimed"])


    def test_version_only_update_reuses_original_guidance_without_publication(self):
        original = self.publish()
        before = {path: path.read_bytes() for path in self.cache_path().parent.iterdir()}
        changed = self.request["applicability"] | dict(client_version="future-client")
        result = self.status(applicability=changed, profile_ids=["small"])
        self.assertTrue(result["usable"])
        self.assertEqual(result["freshness"], "verified")
        self.assertEqual(result["recommendations"], self.guidance["recommendations"])
        for key in ("checked_at", "snapshot_ref", "snapshot_digest"):
            self.assertEqual(result[key], original[key])
        self.assertFalse(self.claim(applicability=changed)["claimed"])
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_failed_refresh_does_not_retry_for_version_only_update(self):
        for published in (False, True):
            with self.subTest(published=published):
                from tests.fixture_canonical_birth import fresh_canonical
                fixture,acquired,_=fresh_canonical(self)
                self.base,self.scope=fixture.base,acquired.resolution.scope
                if published:
                    self.publish()
                claim = self.claim("maintenance", "offline-" + str(published))
                self.api().record_guidance(self.scope, self.request | dict(token=claim["token"],
                    generation=claim["generation"], coordinator_ref="coordinator:offline", failure="Offline"))
                changed = self.request["applicability"] | dict(client_version="future-client")
                result = self.status(applicability=changed)
                self.assertEqual(result["freshness"], "stale" if published else "unavailable")
                self.assertEqual(result["usable"], published)
                self.assertFalse(self.claim(applicability=changed)["claimed"])

    def test_version_only_update_cannot_rebind_an_outstanding_refresh_claim(self):
        claim = self.claim()
        changed = self.request["applicability"] | dict(client_version="future-client")
        with self.assertRaisesRegex(state.StateError, "claim expired, changed or superseded"):
            self.api().record_guidance(self.scope, self.request | dict(applicability=changed,
                token=claim["token"], generation=claim["generation"], coordinator_ref="coordinator:offline", failure="Offline"))
        self.assertTrue(self.record(claim)["usable"])

    def test_capability_change_requires_refresh_without_a_version_change(self):
        self.publish()
        changed = self.request["applicability"] | dict(capability_fingerprint="c" * 64)
        result = self.status(applicability=changed)
        self.assertFalse(result["usable"])
        self.assertEqual(result["freshness"], "incompatible")
        self.assertTrue(self.claim(applicability=changed)["claimed"])


    def test_new_refresh_cannot_relabel_original_guidance_as_current(self):
        self.publish()
        changed = self.request["applicability"] | dict(client_version="future-client")
        claim = self.claim("maintenance", "upgrade", applicability=changed)
        with self.assertRaisesRegex(state.StateError, "publication applicability changed"):
            self.api().record_guidance(self.scope, self.request | dict(applicability=changed,
                token=claim["token"], generation=claim["generation"],
                coordinator_ref="coordinator:verified", guidance=self.guidance))
        updated = self.guidance | dict(applicability=changed)
        self.assertTrue(self.record(claim, applicability=changed, guidance=updated)["usable"])


    def test_legacy_failed_refresh_keeps_stale_guidance_and_trigger_suppression_after_update(self):
        self.publish()
        claim = self.claim('maintenance', 'legacy-offline')
        self.api().record_guidance(self.scope, self.request | dict(token=claim['token'],
            generation=claim['generation'], coordinator_ref='coordinator:offline', failure='Offline'))
        path = self.cache_path().with_name('codex-desktop.control.json')
        control = json.loads(path.read_bytes())
        control['last_attempt']['key'] = hashlib.sha256(json.dumps(dict(applicability=self.request['applicability'],
            prior=hashlib.sha256(self.cache_path().read_bytes()).hexdigest(), snapshot_valid=True),
            sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        path.write_text(json.dumps(control))
        before = path.read_bytes()
        changed = self.request['applicability'] | dict(client_version='future-client')
        self.assertEqual(self.status(applicability=changed)['freshness'], 'stale')
        self.assertFalse(self.claim('maintenance', 'legacy-offline', applicability=changed)['claimed'])
        self.assertFalse(self.claim(applicability=changed)['claimed'])
        self.assertEqual(path.read_bytes(), before)


    def test_missing_legacy_snapshot_rechecks_unknown_applicability_once(self):
        claim = self.claim()
        self.api().record_guidance(self.scope, self.request | dict(token=claim['token'],
            generation=claim['generation'], coordinator_ref='coordinator:offline', failure='Offline'))
        path = self.cache_path().with_name('codex-desktop.control.json')
        control = json.loads(path.read_bytes())
        control['last_attempt']['key'] = hashlib.sha256(json.dumps(dict(applicability=self.request['applicability'],
            prior='missing', snapshot_valid=False), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        path.write_text(json.dumps(control))
        changed = self.request['applicability'] | dict(client_version='future-client')
        replacement = self.claim(applicability=changed)
        self.assertTrue(replacement['claimed'])
        self.api().record_guidance(self.scope, self.request | dict(applicability=changed, token=replacement['token'],
            generation=replacement['generation'], coordinator_ref='coordinator:offline', failure='Offline'))
        later = changed | dict(client_version='another-future-client')
        self.assertFalse(self.claim(applicability=later)['claimed'])
        changed_capability = later | dict(capability_fingerprint='c' * 64)
        self.assertTrue(self.claim(applicability=changed_capability)['claimed'])


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        from tests.test_evidence import EvidenceTests
        from runtime import delegation
        self.api = delegation
        self.assertTrue(hasattr(delegation, 'plan_delegation'), 'assignment admission implementation missing')
        self.fixture = EvidenceTests(methodName='runTest')
        self.fixture._canonical_peer = getattr(self,'_canonical_peer',False) or self._testMethodName in {
            'test_cross_scope_claims_and_explicit_shared_services_conflict',
            'test_unknown_old_check_resources_in_another_checkout_block_dispatch'}
        self.fixture._canonical_linked = self._testMethodName in {
            'test_linked_checkout_git_marker_is_coordinator_owned',
            'test_actual_private_worktree_git_metadata_is_coordinator_owned',
            'test_git_marker_retarget_during_discovery_blocks_commit',
            'test_git_private_alias_retarget_with_unchanged_marker_blocks_commit',
            'test_git_commondir_byte_change_during_discovery_blocks_commit',
            'test_git_common_alias_retarget_after_candidate_capture_blocks_commit'}
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.scope = self.fixture.scope
        if self.fixture._canonical_peer:self.peer_scope=self.fixture.peer_scope
        self.identity = dict(self.fixture.identity)
        state.start_task(self.scope, self.identity | dict(mode='editable', decision_ref='user:accepted'))
        self.fixture.plan()
        self.root, self.repo = self.fixture.root, self.fixture.repo
        from runtime import changes
        guard_patch = patch.object(changes, 'LOCK_ROOT', self.root / 'authority-guards')
        guard_patch.start()
        self.addCleanup(guard_patch.stop)
        self.catalog = dict(version=1, client_surface='codex-cli', native_version='0.163.0',
            parent_session_id='parent', originator='fixture-cli', parent_model_provider='native-provider',
            catalog=[dict(model='new-native-model', supported_efforts=['low', 'high'], default_effort='low')],
            dispatch_controls=dict(spawn=True, explicit_model=True, explicit_effort=True, fork_turns_none=True),
            capability_digest='a' * 64, limitations=['fixture native catalog, no live execution'])
        self.cache_request = dict(client_surface='codex-cli', applicability=dict(client_version='0.163.0',
            capability_fingerprint='a' * 64))
        claim = self.api.claim_guidance(self.scope, self.cache_request | dict(trigger=dict(kind='routine', id='fixture')))
        guidance = dict(schema_version=1, client_surface='codex-cli', checked_at='2026-01-01T00:00:00Z',
            applicability=self.cache_request['applicability'], sources=[dict(id='official',
                url='https://example.org/official', sha256='b' * 64)], recommendations=[dict(id='bounded',
                work='Bounded code work', risk='Local concurrency-sensitive work', models=[],
                conditions=['Coordinator verifies capability'], limitations=['Advice only'], source_ids=['official'])])
        self.api.record_guidance(self.scope, self.cache_request | dict(token=claim['token'],
            generation=claim['generation'], coordinator_ref='fixture:verified', guidance=guidance))
        def discover(scope, controls):
            return deepcopy(self.catalog) | dict(parent_session_id=controls['parent_session_id'])
        self.discovery = patch('runtime.adapters.codex.discover_native_workers', side_effect=discover)
        self.discover = self.discovery.start()
        self.addCleanup(self.discovery.stop)

    def request(self, **fields):
        return self.identity | dict(expected_revision=state.task_status(self.scope,
            'codex', self.identity['session_id'])['task']['revision']) | fields

    def path_claim(self, name='app.py', mode='write'):
        return dict(kind='path', key=str(self.repo / name), mode=mode)

    def plan_value(self, **updates):
        return dict(version=1, route=dict(mechanism='codex-native-subagent', client_surface='codex-cli',
            parent_session_id=self.identity['session_id'], authority_ref='coordinator:verified-existing-native-route',
            controls_ref='coordinator:exposed-tools-checked'), authority=dict(state_dir=str(self.scope.runtime.state_dir),
            participants_ref='coordinator:participating-owners', ownership_known=True),
            selection_constraints=dict(complexity='Coupled code', risk='Concurrency and security',
                rationale='Native worker isolates bounded context', required_controls=['spawn']),
            limits=dict(max_attempts=8, max_resource_units=8, deadline=int(self.api._now()) + 600,
                max_parallel_workers=4, host_capacity=4, capacity_ref='coordinator:observed-slots'),
            fallback=dict(action='stop', reference='user:original-budget'), jobs=[dict(id='edit', role='worker',
                claims=[self.path_claim()], dependencies=[], max_attempts=3)]) | updates

    def plan(self, value=None):
        return self.api.plan_delegation(self.scope, self.request(plan=value or self.plan_value()))

    def prepare_request(self, **updates):
        status = self.api.guidance_status(self.scope, self.cache_request)
        return self.request(dispatch_id='request-1', job_id='edit', resource_units=1,
            brief=dict(objective='Implement bounded edit', context='Inspect existing callers first',
                instructions=['Test first; preserve validation'], files=['app.py'], resources=[self.path_claim()],
                dependencies=[], acceptance=['Required behavior verified'], checks=['unit'],
                self_review='Inspect diff and callers', stop_conditions=['Stop for review after this assignment']),
            selection=dict(model='new-native-model', effort='high', rationale='Capable native model for exact scope',
                profile_ids=['bounded'], mode='explicit'),
            control_observation=dict(version=1, client_surface='codex-cli', native_version=self.catalog['native_version'],
                parent_session_id=self.identity['session_id'], dispatch_controls=deepcopy(self.catalog['dispatch_controls'])),
            discovery_expectation={key: self.catalog[key] for key in
                ('client_surface', 'native_version', 'capability_digest', 'parent_model_provider')} |
                dict(parent_session_id=self.identity['session_id']),
            guidance={key: status[key] for key in ('snapshot_ref', 'snapshot_digest', 'checked_at', 'freshness')}) | updates

    def prepare(self, **updates):
        return self.api.prepare_assignment(self.scope, self.prepare_request(**updates))

    def test_invalid_visibility_does_not_prevent_managed_worker_admission(self):
        self.plan()
        prepared = self.prepare(visibility=dict(version=1, role='Default', specialty='Parser fixes'))
        self.assertTrue(prepared['launch_admitted'])
        self.assertNotIn('visibility', prepared)

    def test_visibility_is_bound_to_worker_replay_without_granting_authority(self):
        self.plan()
        visibility = dict(version=1, role='Builder', specialty='Parser fixes', custom_role_name='Parser owner')
        request = self.prepare_request(visibility=visibility)
        try:
            prepared = self.api.prepare_assignment(self.scope, request)
        except state.StateError as error:
            self.fail('Native worker visibility was rejected: ' + str(error))
        binding = self.api.prepared_native_assignment(self.scope, prepared['attempt_id'])
        self.assertEqual(prepared['visibility'], visibility)
        self.assertEqual(binding['visibility'], visibility)
        self.assertEqual(binding['task_name'], 'worker_' + prepared['attempt_id'])
        body = json.loads(prepared['prompt'])['assignment']
        self.assertEqual(body['role'], 'worker')
        self.assertEqual(body['request']['visibility'], visibility)
        replay = self.api.prepare_assignment(self.scope, request)
        self.assertFalse(replay['launch_admitted'])
        self.assertEqual(replay['attempt_id'], prepared['attempt_id'])
        self.assertEqual(replay['visibility'], visibility)
        for changed in (dict(role='Reviewer'), dict(specialty='Other work'), dict(custom_role_name='Other owner')):
            with self.subTest(changed=changed), self.assertRaisesRegex(state.StateError, 'changed payload'):
                self.api.prepare_assignment(self.scope, request | dict(visibility=visibility | changed))
        status = self.api.delegation_status(self.scope, 'codex', 'parent')
        self.assertEqual(status['budgets']['attempts_consumed'], 1)
        self.assertEqual(status['budgets']['resource_units_consumed'], 1)
        invalid_body = deepcopy(body)
        invalid_body['request']['visibility']['role'] = 'Unknown'
        invalid = self.raw_assignment(prepared['attempt_id'])
        invalid['assignment'] = fixture_artifact(self.scope, self.identity, invalid_body)
        invalid['request_digest'] = evidence._digest(invalid_body['request'])
        with state._database(self.scope) as db:
            row = db.execute('select id,scope_id,client,session_id,task_id,dispatch_id,data from assignments where id=?',
                (prepared['attempt_id'],)).fetchone()
            with self.assertRaisesRegex(state.StateError, 'visibility'):
                self.api._record_data(self.scope, row[:-1] + (json.dumps(invalid),), invalid, db=db)

    def raw_assignment(self, attempt_id):
        with state._database(self.scope) as db:
            return json.loads(db.execute('select data from assignments where id=?', (attempt_id,)).fetchone()[0])

    def settle_fixture(self, attempt_id, status='failed'):
        # Synthetic future reconciliation boundary; never substitutes for live host evidence.
        from runtime import evidence
        with state._database(self.scope, write=True) as db:
            data = self.raw_assignment(attempt_id)
            data.update(status='settled', result=dict(outcome=status), reconciliation=fixture_artifact(
                self.scope,self.identity,dict(fixture='independent stopped-writer boundary', attempt_id=attempt_id,
                    revision=self.request()['expected_revision']),db=db))
            db.execute('update assignments set data=? where id=?', (json.dumps(data), attempt_id))

    def second_owner(self, number, claims=None, limits=None, scope=None):
        from runtime import evidence
        identity = self.identity | dict(session_id='parent-' + str(number), task_id='task-' + str(number))
        proof_plan = evidence._load_plan(self.scope, self.identity)
        owner_scope = scope or self.scope
        state.start_task(owner_scope, identity | dict(mode='editable', decision_ref='user:accepted'))
        evidence.plan_evidence(owner_scope, identity | dict(expected_revision=1, plan=proof_plan))
        prior, prior_scope = self.identity, self.scope
        self.identity, self.scope = identity, owner_scope
        try:
            value = self.plan_value()
            if claims is not None:
                value['jobs'][0]['claims'] = claims
            if limits:
                value['limits'].update(limits)
            self.plan(value)
            request = self.prepare_request()
            request['brief']['resources'] = value['jobs'][0]['claims']
            request['brief']['files'] = [str(Path(c['key']).relative_to(owner_scope.repository_root))
                for c in value['jobs'][0]['claims'] if c['kind'] == 'path' and
                Path(c['key']).is_relative_to(owner_scope.repository_root)]
            return request
        finally:
            self.identity, self.scope = prior, prior_scope

    def test_native_lifecycle_and_new_task_ignore_unrelated_settled_history(self):
        self.plan()
        previous = self.prepare()
        self.settle_fixture(previous['attempt_id'])
        request = self.second_owner(2)
        self.identity = {key: request[key] for key in evidence.IDENTITY[:4]}
        with patch.object(self.api, 'MAX_ASSIGNMENTS', 1):
            prepared = self.api.prepare_assignment(self.scope, request)
            self.assertTrue(prepared['launch_admitted'])
            binding = self.api.prepared_native_assignment(self.scope, prepared['attempt_id'])
            self.assertEqual(binding['parent_session_id'], self.identity['session_id'])
            observed = self.observe_worker(prepared)
            self.assertEqual(observed['attempt_id'], prepared['attempt_id'])
            fresh = state.start_task(self.scope, self.identity | dict(session_id='fresh', task_id='fresh',
                mode='editable', decision_ref='user:accepted'))
            self.assertEqual(fresh['task']['task_id'], 'fresh')

    def test_plan_is_immutable_and_replay_returns_once_only_admission(self):
        self.plan()
        request = self.prepare_request()
        first = self.api.prepare_assignment(self.scope, request)
        self.assertTrue(first['launch_admitted'])
        self.assertFalse(first['replayed'])
        calls = self.discover.call_count
        for _ in range(3):
            replay = self.api.prepare_assignment(self.scope, request)
            self.assertFalse(replay['launch_admitted'])
            self.assertTrue(replay['replayed'])
            self.assertEqual(replay['attempt_id'], first['attempt_id'])
        self.assertEqual(self.discover.call_count, calls)
        current = self.request()
        replay = self.api.prepare_assignment(self.scope, request | dict(expected_revision=current['expected_revision']))
        self.assertFalse(replay['launch_admitted'])
        for delta in (dict(resource_units=2), dict(selection=request['selection'] | dict(effort='low'))):
            with self.assertRaises(state.StateError):
                self.api.prepare_assignment(self.scope, request | delta)
        altered = self.plan_value()
        altered['limits']['max_attempts'] += 1
        with self.assertRaises(state.StateError):
            self.plan(altered)
        report = self.api.delegation_status(self.scope, 'codex', 'parent')
        self.assertEqual(report['budgets']['attempts_consumed'], 1)
        self.assertEqual(report['budgets']['resource_units_consumed'], 1)
        self.assertEqual(len(report['assignments']), 1)
        binding = self.api.prepared_native_assignment(self.scope, first['attempt_id'])
        self.assertEqual(set(binding), {'attempt_id','parent_session_id','task_name','assignment_digest',
            'candidate_digest','requested_model','requested_effort','client_surface','native_version','pins','accepted_identity','scope_id'})
        self.assertEqual(binding['task_name'], 'worker_' + first['attempt_id'])
        self.assertEqual(json.loads(first['prompt'])['assignment_digest'], binding['assignment_digest'])

    def test_fifteen_conflicting_sessions_reserve_exactly_once(self):
        requests = [self.second_owner(n) for n in range(15)]
        barrier = threading.Barrier(15)
        def prepare(request):
            barrier.wait(timeout=10)
            try:
                return self.api.prepare_assignment(self.scope, request)
            except state.StateError:
                return None
        with ThreadPoolExecutor(max_workers=15) as executor:
            results = list(executor.map(prepare, requests))
        self.assertEqual(sum(bool(result and result['launch_admitted']) for result in results), 1)
        with state._database(self.scope) as db:
            self.assertEqual(db.execute('select count(*) from assignments').fetchone()[0], 1)

    def test_actual_discovery_runs_outside_locks_and_caller_expectations_cannot_forge_catalog(self):
        self.plan()
        original = self.discover.side_effect
        def observe(scope, controls):
            with state._database(scope, write=True):
                pass
            return original(scope, controls)
        self.discover.side_effect = observe
        request = self.prepare_request()
        self.catalog['catalog'][0]['model'] = 'different-actual-model'
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, request)
        self.catalog['catalog'][0]['model'] = 'new-native-model'
        for key, value in (('parent_model_provider', None), ('capability_digest', 'f' * 64),
                           ('client_surface', 'codex-desktop')):
            with self.subTest(key=key):
                before = self.catalog[key]
                self.catalog[key] = value
                with self.assertRaises(state.StateError):
                    self.api.prepare_assignment(self.scope, request)
                self.catalog[key] = before
        result = self.api.prepare_assignment(self.scope, request)
        self.assertTrue(result['launch_admitted'])
        data = self.raw_assignment(result['attempt_id'])
        from runtime import evidence
        assignment = evidence._read(self.scope, data['assignment'])
        self.assertEqual(evidence._read(self.scope, assignment['discovery']), self.catalog)
        self.assertIn('policy', assignment)
        self.assertEqual(evidence._read(self.scope, assignment['policy'])['policy_digest'], self.scope.policy_digest)

    def test_path_hardlink_symlink_and_case_conflicts(self):
        self.plan()
        requests = [self.second_owner(number, [self.path_claim(name)])
                    for number, name in enumerate(('hard.py', 'alias.py', 'APP.py'))]
        self.prepare()
        os.link(self.repo / 'app.py', self.repo / 'hard.py')
        (self.repo / 'alias.py').symlink_to(self.repo / 'app.py')
        for request in requests:
            with self.subTest(path=request['brief']['files']):
                with self.assertRaisesRegex(state.StateError, 'Canonical resource ownership conflicts'):
                    self.api.prepare_assignment(self.scope, request)

    def test_directory_ancestor_conflicts(self):
        value = self.plan_value()
        value['jobs'][0]['claims'] = [self.path_claim('work/child.py')]
        self.plan(value)
        request = self.prepare_request()
        request['brief'].update(files=['work/child.py'], resources=value['jobs'][0]['claims'])
        self.api.prepare_assignment(self.scope, request)
        other = self.second_owner(1, [self.path_claim('work')])
        with self.assertRaisesRegex(state.StateError, 'Canonical resource ownership conflicts'):
            self.api.prepare_assignment(self.scope, other)

    def test_disjoint_claims_respect_capacity(self):
        self.plan()
        self.prepare()
        second = self.second_owner(1, [self.path_claim('other.py')], dict(max_parallel_workers=2, host_capacity=2))
        self.assertTrue(self.api.prepare_assignment(self.scope, second)['launch_admitted'])
        third = self.second_owner(2, [self.path_claim('third.py')], dict(max_parallel_workers=2, host_capacity=2))
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, third)

    def test_cross_scope_claims_and_explicit_shared_services_conflict(self):
        shared = dict(kind='service', host='local', key='fixture:shared-db', mode='write')
        value = self.plan_value()
        value['jobs'][0]['claims'] = [shared]
        self.plan(value)
        request = self.prepare_request()
        request['brief'].update(resources=[shared], files=[])
        self.api.prepare_assignment(self.scope, request)
        other_scope = self.peer_scope
        request = self.second_owner(1, [shared], scope=other_scope)
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(other_scope, request)
        request = self.second_owner(2, [shared])
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, request)

    def test_unknown_authority_hard_controls_and_non_native_route_block(self):
        for mutate in (lambda p:p['authority'].update(ownership_known=False),
                       lambda p:p['authority'].update(state_dir=str(self.root / 'different-state')),
                       lambda p:p['route'].update(mechanism='external-api'),
                       lambda p:p['selection_constraints'].update(required_controls=['writable_paths'])):
            with self.subTest(mutate=mutate):
                value = self.plan_value()
                mutate(value)
                with self.assertRaises(state.StateError):
                    self.plan(value)

    def test_plan_jobs_accept_an_optional_task_profile_and_reject_invalid_ones(self):
        value = self.plan_value()
        value['jobs'][0]['profile'] = dict(task_family='implementation', complexity_band='medium',
                                           risk_band='high', review_policy='independent', tools=['edit'])
        self.assertTrue(self.plan(value)['ok'])
        for bad in (dict(complexity_band='enormous'), dict(unknown=1), 'text'):
            invalid = self.plan_value()
            invalid['jobs'][0]['profile'] = bad
            with self.assertRaises(state.StateError):
                self.plan(invalid)

    def test_dependency_cycles_unknowns_and_unsettled_predecessors_block(self):
        value = self.plan_value()
        value['jobs'].append(dict(id='after', role='worker', claims=[self.path_claim('after.py')],
            dependencies=['edit'], max_attempts=1))
        cycle = deepcopy(value)
        cycle['jobs'][0]['dependencies'] = ['after']
        with self.assertRaises(state.StateError):
            self.plan(cycle)
        missing = deepcopy(value)
        missing['jobs'][0]['dependencies'] = ['absent']
        with self.assertRaises(state.StateError):
            self.plan(missing)
        self.plan(value)
        after = self.prepare_request(job_id='after', dispatch_id='after-1')
        after['brief']['resources'] = value['jobs'][1]['claims']
        after['brief']['files'] = ['after.py']
        after['brief']['dependencies'] = ['edit']
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, after)
        first = self.prepare()
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, after | dict(expected_revision=self.request()['expected_revision']))
        self.settle_fixture(first['attempt_id'], 'completed')
        self.assertTrue(self.api.prepare_assignment(self.scope,
            after | dict(expected_revision=self.request()['expected_revision']))['launch_admitted'])

    def test_retry_preserves_original_finite_attempt_and_resource_budgets(self):
        value = self.plan_value()
        value['limits'].update(max_attempts=2, max_resource_units=2)
        value['jobs'][0]['max_attempts'] = 2
        self.plan(value)
        first = self.prepare()
        retry = self.prepare_request(dispatch_id='retry-1', retry_of=first['attempt_id'], diagnostic=dict(
            prior_attempt_id=first['attempt_id'], cause='Fixture failure identified', evidence=['host:new-observation'],
            change='Correct bounded input before retry'))
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, retry)
        self.settle_fixture(first['attempt_id'])
        second = self.api.prepare_assignment(self.scope, retry)
        self.assertTrue(second['launch_admitted'])
        self.settle_fixture(second['attempt_id'])
        retry.update(dispatch_id='retry-2', retry_of=second['attempt_id'], expected_revision=self.request()['expected_revision'])
        retry['diagnostic']['prior_attempt_id'] = second['attempt_id']
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, retry)
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 2)
        with self.assertRaises(state.StateError):
            self.plan(value | dict(fallback=dict(action='direct-coordinator', reference='new:does-not-reset')))

    def test_stale_guidance_is_usable_but_changed_snapshot_and_unsupported_selection_block(self):
        self.plan()
        claim = self.api.claim_guidance(self.scope, self.cache_request | dict(trigger=dict(kind='manual', id='offline')))
        self.api.record_guidance(self.scope, self.cache_request | dict(token=claim['token'], generation=claim['generation'],
            coordinator_ref='coordinator:offline', failure='Offline refresh'))
        request = self.prepare_request()
        self.assertEqual(request['guidance']['freshness'], 'stale')
        for delta in (dict(guidance=request['guidance'] | dict(snapshot_digest='c' * 64)),
                      dict(selection=request['selection'] | dict(effort='unsupported')),
                      dict(selection=request['selection'] | dict(profile_ids=['missing']))):
            with self.assertRaises(state.StateError):
                self.api.prepare_assignment(self.scope, request | delta)
        self.assertTrue(self.api.prepare_assignment(self.scope, request)['launch_admitted'])

    def legacy_unlinked_review_fixture(self, scope, identity):
        # Deliberate retained historical evidence; never a current native launch path.
        plan = evidence._load_plan(scope, identity)
        candidate = evidence.capture_candidate(scope, plan)
        identity_id = __import__('uuid').uuid4().hex
        attempt = dict(id=identity_id, kind='review', status='reserved', candidate=fixture_artifact(scope,identity, candidate),
            candidate_digest=evidence._digest(candidate), plan_digest=evidence._digest(plan), task_name='review_'+identity_id,
            assignment=fixture_artifact(scope,identity, dict(prompt='Legacy unresolved reviewer')), assignment_digest='a'*64,
            coverage=['general'], criteria=['works'])
        with state._database(scope, write=True) as db:
            task = state._task(db, state._scope_id(scope), identity['client'], identity['session_id'])
            task['evidence']['attempts'].append(attempt)
            task['revision'] += 1
            state._save(db, state._scope_id(scope), task)

    def test_deadline_resources_revision_and_unresolved_old_evidence_block(self):
        value = self.plan_value()
        value['limits']['max_resource_units'] = 1
        self.plan(value)
        with self.assertRaises(state.StateError):
            self.prepare(resource_units=2)
        request = self.prepare_request()
        with patch.object(self.api, '_now', return_value=value['limits']['deadline'] + 1):
            with self.assertRaises(state.StateError):
                self.api.prepare_assignment(self.scope, request)
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, request | dict(expected_revision=1))
        self.legacy_unlinked_review_fixture(self.scope, self.identity)
        with self.assertRaises(state.StateError):
            self.prepare()

    def assert_directory_alias_claims_are_exclusive(self, kind, directory_first):
        work = self.repo / 'work'
        work.mkdir()
        (work / 'inside.py').write_text('inside = 1\n')
        outside = self.repo / 'outside.py'
        if kind == 'hardlink':
            os.link(work / 'inside.py', outside)
        else:
            outside.write_text('outside = 1\n')
            (work / 'link.py').symlink_to(outside)
            (self.repo / '.git/info/exclude').write_text('work/link.py\n')
        directory, alias = self.path_claim('work'), self.path_claim('outside.py')
        first, second = (directory, alias) if directory_first else (alias, directory)
        value = self.plan_value()
        value['jobs'][0]['claims'] = [first]
        self.plan(value)
        request = self.prepare_request()
        request['brief'].update(resources=[first], files=['work/inside.py'] if directory_first else ['outside.py'])
        self.assertTrue(self.api.prepare_assignment(self.scope, request)['launch_admitted'])
        other = self.second_owner(1, [second])
        other['brief']['files'] = ['outside.py'] if directory_first else ['work/inside.py']
        with self.assertRaisesRegex(state.StateError, 'Canonical resource ownership conflicts'):
            self.api.prepare_assignment(self.scope, other)

    def test_directory_then_hardlinked_descendant_alias_conflicts(self):
        self.assert_directory_alias_claims_are_exclusive('hardlink', True)

    def test_hardlinked_descendant_alias_then_directory_conflicts(self):
        self.assert_directory_alias_claims_are_exclusive('hardlink', False)

    def test_directory_then_symlink_descendant_target_conflicts(self):
        self.assert_directory_alias_claims_are_exclusive('symlink', True)

    def test_symlink_descendant_target_then_directory_conflicts(self):
        self.assert_directory_alias_claims_are_exclusive('symlink', False)

    def test_linked_checkout_git_marker_is_coordinator_owned(self):
        scope=self.scope
        self.assertTrue((scope.repository_root/'.git').is_file())
        self.assertEqual(Path(self.api._git_directory(scope)),scope.git_dir)
        with self.assertRaises(state.StateError):
            request=self.second_owner(1,[dict(kind='path',key=str(scope.repository_root/'.git'),mode='write')])
            self.api.prepare_assignment(scope,request)

    def test_actual_private_worktree_git_metadata_is_coordinator_owned(self):
        scope=self.scope
        private=Path(subprocess.check_output(['git','-C',str(scope.repository_root),'rev-parse',
            '--path-format=absolute','--git-dir'],text=True).strip())
        self.assertEqual(private,scope.git_dir)
        self.assertEqual(Path(self.api._git_directory(scope)),private)
        # Actual private metadata outside this checkout cannot become a worker write claim.
        with self.assertRaises(state.StateError):
            request=self.second_owner(1,[dict(kind='path',key=str(private/'HEAD'),mode='write')])
            self.api.prepare_assignment(scope,request)

    def operation_request(self, identity=None, **fields):
        identity = identity or self.identity
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), identity['client'], identity['session_id'], identity['task_id'])
        return identity | dict(expected_revision=task['revision'], dispatch_id='operation-1',
            claims=[self.path_claim('coordinator.txt')], operation=dict(id='operation-1', kind='coordinator-work'),
            reason='Bounded model-free operation', ownership_ref='host:authorized-resource-owner',
            stop_conditions=['Stop after this operation']) | fields

    def test_task_only_coordinator_kernel_has_no_invented_native_or_plan_facts(self):
        from runtime import evidence
        identity = self.identity | dict(session_id='coordinator-only', task_id='coordinator-only')
        state.start_task(self.scope, identity | dict(mode='editable', decision_ref='user:accepted'))
        request = self.operation_request(identity)
        with patch.object(evidence, 'capture_candidate', side_effect=AssertionError('invented candidate')):
            with patch.object(self.api, 'guidance_status', side_effect=AssertionError('invented model guidance')):
                result = self.api._reserve_operation(self.scope, request, role='coordinator')
                replay = self.api._reserve_operation(self.scope, request, role='coordinator')
        self.assertTrue(result['reservation_admitted'])
        self.assertFalse(replay['reservation_admitted'])
        self.assertNotIn('launch_admitted', result)
        data = self.raw_assignment(result['attempt_id'])
        for key in ('candidate', 'binding', 'plan_digest', 'host_capacity'):
            self.assertIsNone(data[key])
        self.assertEqual(data['resource_units'], 0)
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), identity['client'], identity['session_id'], identity['task_id'])
        self.assertNotIn('delegation', task)
        self.assertNotIn('evidence', task)

    def test_model_free_rows_do_not_consume_or_reset_native_counters(self):
        self.plan()
        request = self.operation_request()
        result = self.api._reserve_operation(self.scope, request, role='coordinator')
        report = self.api.delegation_status(self.scope, 'codex', 'parent')
        self.assertEqual(report['budgets']['attempts_consumed'], 0)
        self.assertEqual(report['capacity']['unresolved_managed_workers'], 0)
        self.assertTrue(self.prepare()['launch_admitted'])
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 1)
        corrupted = self.raw_assignment(result['attempt_id'])
        corrupted['binding'] = dict(worker_id='fabricated')
        with state._database(self.scope, write=True) as db:
            db.execute('update assignments set data=? where id=?', (json.dumps(corrupted), result['attempt_id']))
        with self.assertRaises(state.StateError):
            self.api.delegation_status(self.scope, 'codex', 'parent')

    def test_task_only_coordinator_proof_binds_operation_claims_and_quiescence(self):
        from runtime import evidence
        identity = self.identity | dict(session_id='proof-coordinator', task_id='proof-coordinator')
        state.start_task(self.scope, identity | dict(mode='editable', decision_ref='user:accepted'))
        result = self.api._reserve_operation(self.scope, self.operation_request(identity), role='coordinator')
        with state._database(self.scope) as db:
            record = next(r for r in self.api._ledger(self.scope, db) if r['id'] == result['attempt_id'])
            task = state._task(db, state._scope_id(self.scope), identity['client'], identity['session_id'], identity['task_id'])
        bindings = dict(operation_id='operation-1', operation_digest=record['body']['operation']['digest'],
                        claims_digest=evidence._digest(record['data']['claims']))
        observed = fixture_artifact(self.scope,self.identity, bindings | dict(observation='fixture:independent-operation-effects'))
        proof = dict(version=2, kind='coordinator-operation-reconciliation', identity=identity,
            expected_pins=task['pins'], expected_revision=task['revision'], scope_id=state._scope_id(self.scope),
            attempt_id=record['id'], dispatch_id='operation-1', assignment_digest=record['data']['assignment']['digest'],
            decision_ref=task['decision_ref'], operation_evidence=observed, resource_observations=observed,
            operation_stopped=True, operation_descendants_stopped=True, observation_ref='host:operation-tree',
            coordinator_ref='coordinator:independently-checked', **bindings)
        self.assertEqual(self.api._validate_operation_proof(self.scope, record, task, proof), proof)
        for delta in (dict(operation_id='another-operation'), dict(claims_digest='f' * 64),
                      dict(operation_descendants_stopped=False), dict(candidate_digest='e' * 64), dict(expected_revision=True),
                      dict(expected_pins=task['pins'] | dict(schema_version=2.0))):
            with self.subTest(delta=delta), self.assertRaises(state.StateError):
                self.api._validate_operation_proof(self.scope, record, task, proof | delta)
        other = dict(task, session_id='other-session', task_id='other-task')
        with self.assertRaises(state.StateError):
            self.api._validate_operation_proof(self.scope, record, other,
                proof | dict(identity={key: other[key] for key in self.identity}))

    def test_coordinator_retains_actual_plan_context_and_requires_matching_proof(self):
        from runtime import evidence
        self.plan()
        result = self.api._reserve_operation(self.scope, self.operation_request(), role='coordinator')
        with state._database(self.scope) as db:
            record = next(r for r in self.api._ledger(self.scope, db) if r['id'] == result['attempt_id'])
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
        self.assertEqual(record['data']['candidate'], task['evidence']['candidate'])
        self.assertEqual(record['body']['d5_plan'], task['evidence']['plan'])
        self.assertEqual(record['body']['d6_plan'], task['delegation']['plan'])
        bindings = dict(operation_id='operation-1', operation_digest=record['body']['operation']['digest'],
                        claims_digest=evidence._digest(record['data']['claims']))
        observed = fixture_artifact(self.scope,self.identity, bindings | dict(observation='fixture:resource-effects'))
        proof = dict(version=2, kind='coordinator-operation-reconciliation', identity=self.identity,
            expected_pins=task['pins'], expected_revision=task['revision'], scope_id=state._scope_id(self.scope),
            attempt_id=record['id'], dispatch_id='operation-1', assignment_digest=record['data']['assignment']['digest'],
            decision_ref=task['decision_ref'], operation_evidence=observed, resource_observations=observed,
            operation_stopped=True, operation_descendants_stopped=True, observation_ref='host:operation-tree',
            coordinator_ref='coordinator:verified', d5_plan_digest=task['evidence']['plan']['digest'],
            d6_plan_digest=task['delegation']['plan']['digest'], candidate_digest=task['evidence']['candidate']['digest'],
            current_candidate_digest=task['evidence']['candidate']['digest'], **bindings)
        self.assertEqual(self.api._validate_operation_proof(self.scope, record, task, proof,
            current_candidate_digest=proof['current_candidate_digest']), proof)
        missing = dict(proof)
        del missing['d6_plan_digest']
        with self.assertRaises(state.StateError):
            self.api._validate_operation_proof(self.scope, record, task, missing,
                current_candidate_digest=proof['current_candidate_digest'])

    def test_check_kernel_retains_real_d5_bindings_without_native_guidance(self):
        from runtime import evidence
        request = self.operation_request(claims=[self.path_claim(mode='read')],
            operation=dict(id='unit', kind='check'))
        with patch.object(evidence, 'capture_candidate', side_effect=AssertionError('kernel invented capture')):
            with patch.object(self.api, 'guidance_status', side_effect=AssertionError('model-free check queried guidance')):
                result = self.api._reserve_operation(self.scope, request, role='check')
        data = self.raw_assignment(result['attempt_id'])
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
        self.assertEqual(data['plan_digest'], task['evidence']['plan']['digest'])
        self.assertEqual(data['candidate'], task['evidence']['candidate'])
        self.assertIsNone(data['binding'])
        self.assertIsNone(data['host_capacity'])
        self.assertEqual(data['resource_units'], 0)
        self.assertNotIn('delegation', task)
        with self.assertRaises(state.StateError):
            self.api._reserve_operation(self.scope, request | dict(dispatch_id='wrong',
                operation=dict(id='missing', kind='check'), expected_revision=task['revision']), role='check')

    def test_check_kernel_proof_requires_actual_plan_execution_and_candidate_context(self):
        from runtime import evidence
        request = self.operation_request(claims=[self.path_claim(mode='read')], operation=dict(id='unit', kind='check'))
        result = self.api._reserve_operation(self.scope, request, role='check')
        with state._database(self.scope) as db:
            record = next(r for r in self.api._ledger(self.scope, db) if r['id'] == result['attempt_id'])
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
        proof = dict(version=2, kind='check-writer-reconciliation', identity=self.identity,
            expected_pins=task['pins'], expected_revision=task['revision'], scope_id=state._scope_id(self.scope),
            attempt_id=record['id'], dispatch_id='operation-1', assignment_digest=record['data']['assignment']['digest'],
            claims_digest=evidence._digest(record['data']['claims']), candidate_digest=record['data']['candidate']['digest'],
            current_candidate_digest=record['data']['candidate']['digest'], plan_digest=record['data']['plan_digest'],
            execution_digest=None, stopped=True, descendants_stopped=True,
            observation_ref='host:check-never-started', coordinator_ref='coordinator:inspected')
        self.assertEqual(self.api._validate_operation_proof(self.scope, record, task, proof,
            current_candidate_digest=proof['current_candidate_digest']), proof)
        for delta in (dict(execution_digest='f' * 64), dict(plan_digest='e' * 64),
                      dict(stopped=False), dict(native_identity={}), dict(current_candidate_digest='d' * 64)):
            with self.subTest(delta=delta), self.assertRaises(state.StateError):
                self.api._validate_operation_proof(self.scope, record, task, proof | delta,
                    current_candidate_digest=proof['current_candidate_digest'])

    def test_model_free_kernel_conflicts_and_changed_replay_cannot_bypass_native_owner(self):
        self.plan()
        first = self.prepare()
        request = self.operation_request(claims=[self.path_claim()])
        with self.assertRaises(state.StateError):
            self.api._reserve_operation(self.scope, request, role='coordinator')
        other = self.operation_request(claims=[self.path_claim('independent.txt')])
        reserved = self.api._reserve_operation(self.scope, other, role='coordinator')
        with self.assertRaises(state.StateError):
            self.api._reserve_operation(self.scope, other | dict(reason='changed payload'), role='coordinator')
        self.assertEqual(self.raw_assignment(first['attempt_id'])['status'], 'prepared')
        self.assertEqual(self.raw_assignment(reserved['attempt_id'])['status'], 'prepared')

    def test_native_kernel_proof_rejects_fake_observation_and_missing_candidate_binding(self):
        from runtime import evidence
        self.plan()
        result = self.prepare()
        with state._database(self.scope) as db:
            record = next(r for r in self.api._ledger(self.scope, db) if r['id'] == result['attempt_id'])
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
        binding = record['data']['binding']
        native = {key: binding[key] for key in ('client_surface', 'native_version', 'parent_session_id', 'task_name')}
        native.update(parent_turn_id=None, spawn_item_id=None, worker_id=None, worker_turn_id=None, result_item_id=None)
        proof = dict(version=2, kind='assignment-writer-reconciliation', identity=self.identity,
            expected_pins=task['pins'], expected_revision=task['revision'], scope_id=state._scope_id(self.scope),
            attempt_id=record['id'], dispatch_id='request-1', assignment_digest=record['data']['assignment']['digest'],
            candidate_digest=record['data']['candidate']['digest'], current_candidate_digest=record['data']['candidate']['digest'],
            d6_plan_digest=record['data']['plan_digest'], claims_digest=evidence._digest(record['data']['claims']),
            observation_digest=None, result_digest=None, native_identity=native, launch_state='not-started',
            stopped=True, descendants_stopped=True, observation_ref='host:searched-exact-parent-marker',
            coordinator_ref='coordinator:verified-not-launched')
        self.assertEqual(self.api._validate_operation_proof(self.scope, record, task, proof,
            current_candidate_digest=proof['current_candidate_digest']), proof)
        with self.assertRaises(state.StateError):
            self.api._validate_operation_proof(self.scope, record, task, proof)
        for delta in (dict(launch_state='observed'), dict(descendants_stopped=False),
                      dict(native_identity=native | dict(worker_id='unobserved-child')), dict(d6_plan_digest='f' * 64)):
            with self.subTest(delta=delta), self.assertRaises(state.StateError):
                self.api._validate_operation_proof(self.scope, record, task, proof | delta,
                    current_candidate_digest=proof['current_candidate_digest'])

    def test_history_bound_rejects_fresh_reservation_but_preserves_full_ledger_replay(self):
        self.plan()
        with patch.object(self.api, 'MAX_ASSIGNMENTS', 1):
            request = self.operation_request(claims=[self.path_claim(mode='read')])
            first = self.api._reserve_operation(self.scope, request, role='coordinator')
            with state._database(self.scope) as db:
                before = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
                original = db.execute('select * from assignments').fetchall()
            other = self.operation_request(dispatch_id='operation-2', claims=[self.path_claim(mode='read')],
                operation=dict(id='operation-2', kind='coordinator-work'))
            with self.assertRaisesRegex(state.StateError, 'history.*bound'):
                self.api._reserve_operation(self.scope, other, role='coordinator')
            replay = self.api._reserve_operation(self.scope, request | dict(expected_revision=before['revision']), role='coordinator')
            self.assertEqual(replay['attempt_id'], first['attempt_id'])
            self.assertTrue(replay['replayed'])
            self.assertFalse(replay['reservation_admitted'])
            with self.assertRaises(state.StateError):
                self.api._reserve_operation(self.scope, request | dict(reason='changed'), role='coordinator')
            with state._database(self.scope) as db:
                self.assertEqual(state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task'), before)
                self.assertEqual(db.execute('select * from assignments').fetchall(), original)
            status = self.api.delegation_status(self.scope, 'codex', 'parent')
            self.assertEqual(len(status['assignments']), 1)
            self.assertEqual(status['budgets']['attempts_consumed'], 0)
            self.assertEqual(status['budgets']['resource_units_consumed'], 0)

    def test_completed_check_dependency_admits_native_without_consuming_native_budget(self):
        value = self.plan_value()
        value['jobs'].append(dict(id='unit', role='check', claims=[self.path_claim(mode='read')],
            dependencies=[], max_attempts=1))
        value['jobs'][0]['dependencies'] = ['unit']
        self.plan(value)
        check = self.api._reserve_operation(self.scope, self.operation_request(claims=[self.path_claim(mode='read')],
            operation=dict(id='unit', kind='check')), role='check')
        request = self.prepare_request()
        request['brief']['dependencies'] = ['unit']
        with self.assertRaisesRegex(state.StateError, 'dependency.*complete'):
            self.api.prepare_assignment(self.scope, request)
        self.settle_fixture(check['attempt_id'], status='failed')
        with self.assertRaisesRegex(state.StateError, 'dependency.*complete'):
            self.api.prepare_assignment(self.scope, request)
        self.settle_fixture(check['attempt_id'], status='completed')
        native = self.api.prepare_assignment(self.scope, request)
        self.assertTrue(native['launch_admitted'])
        self.assertFalse(self.api.prepare_assignment(self.scope, request)['launch_admitted'])
        status = self.api.delegation_status(self.scope, 'codex', 'parent')
        self.assertEqual(status['budgets']['attempts_consumed'], 1)
        self.assertEqual(status['budgets']['resource_units_consumed'], 1)
        self.assertEqual(status['capacity']['unresolved_managed_workers'], 1)
        self.assertEqual(len(status['assignments']), 2)

    def test_check_with_same_name_cannot_satisfy_a_planned_native_dependency(self):
        value = self.plan_value()
        value['jobs'].append(dict(id='unit', role='worker', claims=[self.path_claim(mode='read')],
            dependencies=[], max_attempts=1))
        value['jobs'][0]['dependencies'] = ['unit']
        self.plan(value)
        check = self.api._reserve_operation(self.scope, self.operation_request(claims=[self.path_claim(mode='read')],
            operation=dict(id='unit', kind='check')), role='check')
        self.settle_fixture(check['attempt_id'], status='completed')
        request = self.prepare_request()
        request['brief']['dependencies'] = ['unit']
        with self.assertRaisesRegex(state.StateError, 'dependency.*complete'):
            self.api.prepare_assignment(self.scope, request)
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 0)

    def test_role_records_reject_invented_fields_and_mismatched_original_bindings(self):
        from copy import deepcopy
        from runtime import evidence
        reserved = self.api._reserve_operation(self.scope, self.operation_request(), role='coordinator')
        with state._database(self.scope) as db:
            original = next(r for r in self.api._ledger(self.scope, db) if r['id'] == reserved['attempt_id'])
        for mutation in ('native-fact', 'owner', 'schema-type'):
            with self.subTest(mutation=mutation):
                data, body = deepcopy(original['data']), deepcopy(original['body'])
                if mutation == 'native-fact':
                    body['discovery'] = dict(model='invented-model')
                elif mutation == 'owner':
                    body['request']['task_id'] = 'another-task'
                    data['request_digest'] = evidence._digest(body['request'])
                else:
                    body['pins']['schema_version'] = 2.0
                data['assignment'] = fixture_artifact(self.scope,self.identity, body)
                owner = original['owner']
                row = (original['id'], *(owner[k] for k in ('scope_id', 'client', 'session_id', 'task_id', 'dispatch_id')),
                       evidence._raw(data).decode())
                with self.assertRaises(state.StateError):
                    self.api._record(self.scope, row)

    def test_worker_check_and_coordinator_mutations_share_the_same_ownership_ledger(self):
        from runtime import evidence
        self.plan()
        worker = self.prepare()
        with patch.object(evidence, '_execute', side_effect=AssertionError('conflicting check executed')):
            with self.assertRaises(state.StateError):
                evidence.run_check(self.scope, self.request(check_id='unit'))
        self.settle_fixture(worker['attempt_id'], status='completed')
        checked = evidence.run_check(self.scope, self.request(check_id='unit'))
        self.assertEqual(checked['attempt']['status'], 'uncertain')
        with self.assertRaises(state.StateError):
            self.api.reserve_coordinator(self.scope, self.operation_request(claims=[self.path_claim()]))
        another = self.second_owner(1, [self.path_claim()])
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, another)

    def lifecycle_observation(self, prepared, *, turn_status='completed', **changes):
        from runtime import evidence
        binding = self.api.prepared_native_assignment(self.scope, prepared['attempt_id'])
        report = dict(version=1, attempt_id=prepared['attempt_id'], assignment_digest=binding['assignment_digest'],
            candidate_digest=binding['candidate_digest'], status='completed', changed_paths=['app.py'],
            evidence=['fixture:worker-report'], limitations=['Independently verify all claims'])
        result = json.dumps(report) if turn_status == 'completed' else None
        receipt = dict(version=2, mechanism='codex-native-agent-path', attempt_id=prepared['attempt_id'],
            task_name=binding['task_name'], agent_path='/root/' + binding['task_name'], native_version=binding['native_version'],
            client_surface=binding['client_surface'], originator='codex_exec', parent_model_provider=None,
            worker_model_provider=None, parent_session_id=binding['parent_session_id'], parent_turn_id='parent-turn',
            spawn_item_id='spawn-item', worker_id='child', worker_turn_id='child-turn', turn_status=turn_status,
            result_item_id='final-item' if result else None, checkout=str(self.repo),
            assignment_digest=binding['assignment_digest'], candidate_digest=binding['candidate_digest'],
            requested_model=binding['requested_model'], requested_effort=binding['requested_effort'],
            configured_model='new-native-model', configured_effort='high', spawn_observed_model='new-native-model',
            spawn_observed_effort='high', execution_model=None, execution_effort=None, descendants_stopped=False,
            prompt_delivery='unverified', result_digest=__import__('hashlib').sha256(result.encode()).hexdigest() if result else None)
        return dict(receipt=receipt | changes, result=result)

    def observe_worker(self, prepared, observed=None, request=None):
        request = request or self.request(attempt_id=prepared['attempt_id'], parent_turn_id='parent-turn', spawn_item_id='spawn-item')
        with patch('runtime.adapters.codex.observe_native_worker', return_value=observed or self.lifecycle_observation(prepared)):
            return self.api.observe_assignment(self.scope, request)

    def worker_reconciliation_request(self, prepared, *, outcome='completed', proof_delta=None, **assessment_delta):
        import hashlib
        from runtime import evidence
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
            record = next(r for r in self.api._ledger(self.scope, db) if r['id'] == prepared['attempt_id'])
        data, binding = record['data'], record['data']['binding']
        observed = evidence._read(self.scope, data['observation']) if data['observation'] else None
        receipt = observed['receipt'] if observed and observed['receipt'] else {}
        native = {key: binding[key] for key in ('client_surface', 'native_version', 'parent_session_id', 'task_name')}
        native.update({key: receipt.get(key) for key in ('parent_turn_id', 'spawn_item_id', 'worker_id', 'worker_turn_id', 'result_item_id')})
        proof = dict(version=2, kind='assignment-writer-reconciliation', identity=self.identity,
            expected_pins=task['pins'], expected_revision=task['revision'], scope_id=state._scope_id(self.scope),
            attempt_id=prepared['attempt_id'], dispatch_id=record['owner']['dispatch_id'],
            assignment_digest=data['assignment']['digest'], candidate_digest=data['candidate']['digest'],
            current_candidate_digest=evidence._digest(evidence.capture_candidate(self.scope, evidence._read(self.scope, task['evidence']['plan']))),
            d6_plan_digest=data['plan_digest'], claims_digest=evidence._digest(data['claims']),
            observation_digest=data['observation']['digest'] if data['observation'] else None,
            result_digest=receipt.get('result_digest'), native_identity=native,
            launch_state='observed' if data['observation'] else 'not-started', stopped=True, descendants_stopped=True,
            observation_ref='host:exact-parent-marker-and-process-tree', coordinator_ref='coordinator:independently-verified')
        proof.update(proof_delta or {})
        path = self.root / ('worker-proof-' + __import__('uuid').uuid4().hex + '.json')
        raw = json.dumps(proof).encode(); path.write_bytes(raw); path.chmod(0o600)
        assessment = dict(outcome=outcome, changed_paths=['app.py'] if outcome == 'completed' else [],
            result_assessment='Independent inspection of worker result', partial_change_assessment='Inspected repository and shared-resource effects',
            evidence=['fixture:independent-inspection'], coordinator_ref='coordinator:assessed') | assessment_delta
        return self.identity | dict(expected_revision=task['revision'], attempt_id=prepared['attempt_id'],
            host_proof=dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest()), assessment=assessment)

    def test_worker_reconcile_binds_changed_candidate_and_quiescence_without_passing_d5(self):
        from runtime import evidence, changes
        self.plan()
        prepared = self.prepare()
        self.observe_worker(prepared)
        (self.repo / 'app.py').write_text('changed by fixture worker\n')
        bad = self.worker_reconciliation_request(prepared, proof_delta=dict(descendants_stopped=False))
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, bad)
        omitted = self.worker_reconciliation_request(prepared, changed_paths=[])
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, omitted)
        request = self.worker_reconciliation_request(prepared)
        original_capture = evidence.capture_candidate
        def capture(*args):
            with state._database(self.scope, write=True), changes._authority_guards([self.scope.runtime.state_dir]):
                pass
            return original_capture(*args)
        with patch.object(evidence, 'capture_candidate', side_effect=capture):
            result = self.api.reconcile_assignment(self.scope, request)
        self.assertEqual(result['status'], 'settled')
        self.assertEqual(result['outcome'], 'completed')
        with patch.object(evidence, 'capture_candidate', side_effect=AssertionError('replay recaptured')):
            self.assertTrue(self.api.reconcile_assignment(self.scope, request)['replayed'])
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, request | dict(assessment=request['assessment'] | dict(outcome='failed')))
        data = self.raw_assignment(prepared['attempt_id'])
        self.assertEqual(data['result']['outcome'], 'completed')
        self.assertIsNotNone(data['reconciliation'])
        report = self.api.delegation_status(self.scope, 'codex', 'parent')
        self.assertEqual(report['capacity']['unresolved_managed_workers'], 0)
        self.assertEqual(report['budgets']['attempts_consumed'], 1)
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
        self.assertEqual(task['evidence']['attempts'], [])
        self.assertNotEqual(task['state'], 'DONE')

    def cooperative_worker(self, prepared, *, version=3, activity=None):
        from runtime import evidence
        if activity is None:
            activity = dict(source_digest='a' * 64, calls=[dict(id='patch-1', tool='apply_patch', input_digest='b' * 64)])
            (self.repo / 'app.py').write_text('changed by fixture worker\n')
        observed = self.lifecycle_observation(prepared) | dict(activity=activity)
        self.observe_worker(prepared, observed, self.request(attempt_id=prepared['attempt_id'],
            parent_turn_id='parent-turn', spawn_item_id='spawn-item', cooperative=True))
        request = self.worker_reconciliation_request(prepared)
        path = Path(request['host_proof']['path'])
        proof = json.loads(path.read_bytes())
        for key in ('stopped', 'descendants_stopped'):
            del proof[key]
        proof.update(version=version, kind='native-cooperative-completion', activity_digest=evidence._digest(activity),
                     coordinator_ref=request['assessment']['coordinator_ref'],
                     observation_ref=request['assessment']['evidence'][0])
        if version == 2:
            proof['no_pending_continuation'] = True
        else:
            proof.update(no_outstanding_coordinator_followups=True, coordinator_submissions_paused=True)
        raw = evidence._raw(proof)
        path.write_bytes(raw)
        request['host_proof']['sha256'] = hashlib.sha256(raw).hexdigest()
        return request, observed

    def test_foreground_wait_is_bound_to_prepared_worker_and_replay(self):
        import sys
        directory = self.root / 'observer'
        directory.mkdir(mode=0o700)
        marker = directory / 'ready'
        wait = dict(marker=str(marker), python=str(Path(sys.executable).resolve()), seconds=15)
        plan = self.plan_value()
        claims = plan['jobs'][0]['claims']
        claims.append(dict(kind='path', key=str(marker), mode='read'))
        self.plan(plan)
        request = self.prepare_request()
        request['brief'].update(resources=claims, foreground_wait=wait)
        prepared = self.api.prepare_assignment(self.scope, request)
        self.assertEqual(self.api.prepared_native_assignment(self.scope, prepared['attempt_id'])['foreground_wait'], wait)
        self.assertEqual(json.loads(prepared['prompt'])['assignment']['request']['brief']['foreground_wait'], wait)
        self.assertFalse(self.api.prepare_assignment(self.scope, request)['launch_admitted'])
        another = request | dict(dispatch_id='other-wait', expected_revision=self.request()['expected_revision'])
        with self.assertRaisesRegex(state.StateError, 'already belongs'):
            self.api.prepare_assignment(self.scope, another)
        request['brief']['foreground_wait'] = wait | dict(seconds=16)
        with self.assertRaisesRegex(state.StateError, 'changed payload'):
            self.api.prepare_assignment(self.scope, request)

    def test_foreground_wait_rejects_unreserved_or_preexisting_marker(self):
        import sys
        directory = self.root / 'observer'
        directory.mkdir(mode=0o700)
        marker = directory / 'ready'
        wait = dict(marker=str(marker), python=str(Path(sys.executable).resolve()), seconds=15)
        plan = self.plan_value()
        claims = plan['jobs'][0]['claims']
        claims.append(dict(kind='path', key=str(marker), mode='read'))
        self.plan(plan)
        request = self.prepare_request()
        request['brief'].update(resources=claims, foreground_wait=wait | dict(marker=str(directory / 'other')))
        with self.assertRaisesRegex(state.StateError, 'exact read claim'):
            self.api.prepare_assignment(self.scope, request)
        request['brief']['foreground_wait'] = wait
        marker.write_text('stale marker')
        with self.assertRaisesRegex(state.StateError, 'fresh marker'):
            self.api.prepare_assignment(self.scope, request)
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 0)

    def test_cooperative_completion_releases_scoped_claims_and_preserves_strict_blockers(self):
        from runtime import evidence
        plan = self.plan_value()
        plan['limits']['max_parallel_workers'] = 1
        plan['jobs'].append(dict(plan['jobs'][0], id='followup', dependencies=['edit']))
        self.plan(plan)
        prepared = self.prepare()
        request, observed = self.cooperative_worker(prepared)
        with patch('runtime.adapters.codex.observe_native_worker', return_value=observed) as observer:
            result = self.api.reconcile_assignment(self.scope, request)
            observer.assert_called_once_with(self.scope, prepared['attempt_id'], 'parent-turn', 'spawn-item', cooperative=True)
        self.assertEqual(result['status'], 'cooperative-completed')
        self.assertTrue(self.api.reconcile_assignment(self.scope, request)['replayed'])
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, request | dict(assessment=request['assessment'] |
                dict(result_assessment='Different assessment for the completed assignment')))
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 1)
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
            records = self.api._ledger(self.scope, db, task=task)
            claims = records[0]['data']['claims']
            self.api._resources_available(self.scope, db, claims, records, cooperative_task=task)
            with self.assertRaisesRegex(state.StateError, 'unresolved assignment'):
                self.api._resources_available(self.scope, db, claims, records)
            self.assertTrue(evidence._ledger_blockers(self.scope, db, claims, records=records))
            artifact = evidence._read(self.scope, records[0]['data']['reconciliation'])
            self.assertNotIn('no_pending_continuation', artifact['proof'])
            self.assertNotIn('descendants_stopped', artifact['proof'])
        self.assertEqual(self.fixture.checked()['attempt']['status'], 'passed')
        later = self.prepare_request(dispatch_id='followup-request', job_id='followup')
        later['brief']['dependencies'] = ['edit']
        self.assertTrue(self.api.prepare_assignment(self.scope, later)['launch_admitted'])

    def test_cooperative_completion_can_be_upgraded_only_with_original_strict_proof(self):
        from runtime import evidence
        self.plan()
        prepared = self.prepare()
        request, observed = self.cooperative_worker(prepared, version=2)
        with patch('runtime.adapters.codex.observe_native_worker', return_value=observed):
            self.api.reconcile_assignment(self.scope, request)
        before = self.raw_assignment(prepared['attempt_id'])
        for proof_delta in (dict(descendants_stopped=False), dict(version=3)):
            with self.assertRaises(state.StateError):
                self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(prepared, proof_delta=proof_delta))
        self.assertEqual(self.raw_assignment(prepared['attempt_id']), before)
        with state._database(self.scope) as db:
            continuation = self.request(attempt_id=prepared['attempt_id'])
            records = self.api._ledger(self.scope, db, task=continuation)
            with self.assertRaisesRegex(state.StateError, 'new admission'):
                self.api._continuation_owned(self.scope, db, continuation, records, allow_settled=True)
        strict = self.worker_reconciliation_request(prepared)
        self.assertEqual(self.api.reconcile_assignment(self.scope, strict)['status'], 'settled')
        after = self.raw_assignment(prepared['attempt_id'])
        self.assertEqual(evidence._read(self.scope, after['reconciliation'])['prior_cooperative'], before['reconciliation'])
        self.assertTrue(self.api.reconcile_assignment(self.scope, request)['replayed'])

    def test_cooperative_completion_rejects_pending_or_unbound_coordinator_assessment(self):
        self.plan()
        prepared = self.prepare()
        request, observed = self.cooperative_worker(prepared)
        path = Path(request['host_proof']['path'])
        proof = json.loads(path.read_bytes())
        before = self.raw_assignment(prepared['attempt_id'])
        invalid = {
            'follow-up outstanding': proof | dict(no_outstanding_coordinator_followups=False),
            'submissions still open': proof | dict(coordinator_submissions_paused=False),
            'missing pause attestation': {key: value for key, value in proof.items() if key != 'coordinator_submissions_paused'},
            'non-boolean pause attestation': proof | dict(coordinator_submissions_paused=1),
            'legacy attestation mixed in': proof | dict(no_pending_continuation=True),
            'legacy version with new attestations': proof | dict(version=2),
            'wrong native identity': proof | dict(native_identity=proof['native_identity'] | dict(worker_id='another-child')),
            'different assessment': proof | dict(coordinator_ref='fixture:unbound-coordinator'),
            'different observation': proof | dict(observation_ref='fixture:unbound-observation'),
        }
        with patch('runtime.adapters.codex.observe_native_worker', return_value=observed):
            for case, candidate in invalid.items():
                with self.subTest(case=case):
                    raw = evidence._raw(candidate)
                    path.write_bytes(raw)
                    request['host_proof']['sha256'] = hashlib.sha256(raw).hexdigest()
                    with self.assertRaises(state.StateError):
                        self.api.reconcile_assignment(self.scope, request)
                    self.assertEqual(self.raw_assignment(prepared['attempt_id']), before)
        status = self.api.delegation_status(self.scope, 'codex', 'parent')
        self.assertEqual(status['capacity']['unresolved_managed_workers'], 1)
        self.assertEqual(status['budgets']['attempts_consumed'], 1)
        self.assertEqual(status['budgets']['resource_units_consumed'], 1)
        self.assertTrue(status['conflicts'])

    def test_cooperative_completion_rejects_fresh_activity_or_candidate_changes(self):
        from runtime.startup import StartupError
        self.plan()
        prepared = self.prepare()
        request, observed = self.cooperative_worker(prepared)
        for changed in (observed | dict(activity=observed['activity'] | dict(source_digest='c' * 64)),
                        observed | dict(receipt=observed['receipt'] | dict(will_retry=True)),
                        observed | dict(receipt=observed['receipt'] | dict(turn_status='interrupted'), result=None)):
            with patch('runtime.adapters.codex.observe_native_worker', return_value=changed):
                with self.assertRaises(state.StateError):
                    self.api.reconcile_assignment(self.scope, request)
        with patch('runtime.adapters.codex.observe_native_worker', side_effect=StartupError('Missing foreground result')):
            with self.assertRaises(state.StateError):
                self.api.reconcile_assignment(self.scope, request)
        (self.repo / 'app.py').write_text('unreviewed change\n')
        with patch('runtime.adapters.codex.observe_native_worker', return_value=observed):
            with self.assertRaises(state.StateError):
                self.api.reconcile_assignment(self.scope, request)
        self.assertEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'observed')
        status = self.api.delegation_status(self.scope, 'codex', 'parent')
        self.assertEqual(status['capacity']['unresolved_managed_workers'], 1)
        self.assertEqual(status['budgets']['attempts_consumed'], 1)
        self.assertTrue(status['conflicts'])

    def test_never_started_worker_requires_exact_proof_and_cancellation_retains_budget(self):
        self.plan()
        prepared = self.prepare()
        bad = self.worker_reconciliation_request(prepared, outcome='cancelled', proof_delta=dict(candidate_digest='f' * 64))
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, bad)
        bad = self.worker_reconciliation_request(prepared, outcome='cancelled', partial_change_assessment='')
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, bad)
        task = state.task_status(self.scope, 'codex', 'parent')['task']
        state.update_task(self.scope, self.identity | dict(expected_revision=task['revision'], state='CANCELLED',
            reason='Cancel intent', reference='user:cancel'))
        result = self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(prepared, outcome='cancelled'))
        self.assertEqual(result['outcome'], 'cancelled')
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 1)
        current = state.task_status(self.scope, 'codex', 'parent')['task']
        replacement = state.start_task(self.scope, self.identity | dict(task_id='replacement', mode='editable',
            decision_ref='user:accepted', replaces=dict(task_id='task', expected_revision=current['revision'])))
        self.assertEqual(replacement['task']['task_id'], 'replacement')
        with state._database(self.scope) as db:
            historical = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
        self.assertEqual(historical['pins'], current['pins'])
        self.assertEqual(historical['delegation']['attempts_consumed'], 1)
        self.assertEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'settled')

    def test_failed_observation_and_invalid_report_keep_ownership_until_exact_failed_reconciliation(self):
        from runtime.startup import StartupError
        self.plan()
        prepared = self.prepare()
        request = self.request(attempt_id=prepared['attempt_id'], parent_turn_id='parent-turn', spawn_item_id='spawn-item')
        with patch('runtime.adapters.codex.observe_native_worker', side_effect=StartupError('fixture unavailable')):
            result = self.api.observe_assignment(self.scope, request)
        self.assertEqual(result['status'], 'uncertain')
        no_child = self.worker_reconciliation_request(prepared, outcome='CANCELLED', proof_delta=dict(launch_state='not-started'))
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, no_child)
        malformed = self.lifecycle_observation(prepared)
        malformed['result'] = '{"status":"completed"}'
        malformed['receipt']['result_digest'] = __import__('hashlib').sha256(malformed['result'].encode()).hexdigest()
        self.assertEqual(self.observe_worker(prepared, malformed)['status'], 'uncertain')
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(prepared))
        result = self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(prepared, outcome='failed'))
        self.assertEqual(result['outcome'], 'failed')
        retry = self.api.prepare_assignment(self.scope, self.prepare_request(dispatch_id='retry-2', retry_of=prepared['attempt_id'],
            diagnostic=dict(prior_attempt_id=prepared['attempt_id'], cause='Invalid result format',
                evidence=['fixture:new-independent-diagnosis'], change='Restate exact report contract')))
        self.assertTrue(retry['launch_admitted'])
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 2)

    def test_reconciliation_reads_ledger_once_per_transaction_around_capture(self):
        self.plan()
        prepared = self.prepare()
        self.observe_worker(prepared)
        request = self.worker_reconciliation_request(prepared)
        actual_ledger, actual_capture = self.api._ledger, evidence.capture_candidate
        captured, reads = [], []

        def ledger(scope, db, **options):
            reads.append((bool(captured), db.in_transaction))
            return actual_ledger(scope, db, **options)

        def capture(*args):
            candidate = actual_capture(*args)
            captured.append(True)
            return candidate

        with patch.object(self.api, '_ledger', side_effect=ledger), \
                patch.object(evidence, 'capture_candidate', side_effect=capture):
            result = self.api.reconcile_assignment(self.scope, request)
        self.assertEqual(result['status'], 'settled')
        self.assertEqual(reads, [(False, True), (True, True)])

    def test_reconciliation_race_cannot_release_changed_revision(self):
        from runtime import evidence
        self.plan()
        prepared = self.prepare()
        self.observe_worker(prepared)
        request = self.worker_reconciliation_request(prepared)
        original_capture = evidence.capture_candidate
        def capture(*args):
            candidate = original_capture(*args)
            task = state.task_status(self.scope, 'codex', 'parent')['task']
            state.update_task(self.scope, self.identity | dict(expected_revision=task['revision'], state='REVIEW',
                reason='Intervening coordinator update', reference='fixture:race'))
            return candidate
        with patch.object(evidence, 'capture_candidate', side_effect=capture), self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, request)
        self.assertNotEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'settled')

    def test_retirement_cannot_forget_unresolved_historical_worker(self):
        import hashlib
        self.plan()
        prepared = self.prepare()
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
        proof = dict(version=1, kind='stopped-writer-reconciliation', identity=self.identity, expected_revision=task['revision'],
            expected_pins=task['pins'], observed_turn_id=task['turn_id'], stopped=True, descendants_stopped=True,
            attempt_ids=[], observation_ref='host:coordinator-stopped', coordinator_ref='coordinator:checked')
        path = self.root / 'retire-proof.json'; raw = json.dumps(proof).encode(); path.write_bytes(raw); path.chmod(0o600)
        request = self.identity | dict(expected_revision=task['revision'], expected_pins=task['pins'], reason='Retire',
            reference='host:inspected', stop_proof=dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest()))
        with self.assertRaisesRegex(state.StateError, 'assignment'):
            state.retire_task(self.scope, request)
        # Construct only a historical binding fixture; supported replacement is separately blocked above.
        with state._database(self.scope, write=True) as db:
            db.execute('delete from bindings where scope_id=?', (state._scope_id(self.scope),))
        with self.assertRaisesRegex(state.StateError, 'assignment'):
            state.start_task(self.scope, self.identity | dict(task_id='new', mode='editable', decision_ref='user:accepted'))
        self.assertEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'prepared')

    def test_worker_observation_is_immutable_idempotent_and_outside_locks(self):
        from runtime import evidence, changes
        self.plan()
        prepared = self.prepare()
        observed = self.lifecycle_observation(prepared)
        request = self.request(attempt_id=prepared['attempt_id'], parent_turn_id='parent-turn', spawn_item_id='spawn-item')
        def observe(*args):
            with state._database(self.scope, write=True), changes._authority_guards([self.scope.runtime.state_dir]):
                pass
            return observed
        with patch('runtime.adapters.codex.observe_native_worker', side_effect=observe):
            first = self.api.observe_assignment(self.scope, request)
            duplicate = self.api.observe_assignment(self.scope, request)
        self.assertEqual(first['revision'], duplicate['revision'])
        self.assertEqual(first['observation_digest'], duplicate['observation_digest'])
        self.assertTrue(duplicate['replayed'])
        from runtime.startup import StartupError
        failure_request = self.request(attempt_id=prepared['attempt_id'], parent_turn_id='parent-turn', spawn_item_id='spawn-item')
        with patch('runtime.adapters.codex.observe_native_worker', side_effect=StartupError('fixture unavailable')):
            self.assertEqual(self.api.observe_assignment(self.scope, failure_request)['status'], 'uncertain')
        self.assertEqual(self.observe_worker(prepared, observed)['status'], 'observed')
        data = self.raw_assignment(prepared['attempt_id'])
        self.assertEqual(data['status'], 'observed')
        self.assertIsNone(data['reconciliation'])
        self.assertIsNone(data['result'])
        artifact = evidence._read(self.scope, data['observation'])
        self.assertEqual(artifact['receipt'], observed['receipt'])
        self.assertEqual(artifact['result'], observed['result'])
        self.assertEqual(artifact['report']['status'], 'completed')
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
        self.assertEqual(task['evidence']['attempts'], [])
        self.assertEqual(task['delegation']['attempts_consumed'], 1)

    def test_worker_progress_retains_history_and_conflicting_child_is_uncertain(self):
        from runtime import evidence
        self.plan()
        prepared = self.prepare()
        first = self.observe_worker(prepared, self.lifecycle_observation(prepared, turn_status='inProgress'))
        second = self.observe_worker(prepared, self.lifecycle_observation(prepared, worker_turn_id='child-turn-2',
            result_item_id='final-item-2', configured_model='observed-different-model'))
        self.assertGreater(second['revision'], first['revision'])
        latest = self.raw_assignment(prepared['attempt_id'])['observation']
        artifact = evidence._read(self.scope, latest)
        self.assertEqual(evidence._read(self.scope, artifact['previous'])['receipt']['turn_status'], 'inProgress')
        self.assertEqual(artifact['receipt']['worker_turn_id'], 'child-turn-2')
        self.assertEqual(artifact['receipt']['configured_model'], 'observed-different-model')
        self.assertEqual(artifact['receipt']['requested_model'], 'new-native-model')
        self.assertIsNone(artifact['receipt']['execution_model'])
        self.assertIsNone(artifact['receipt']['worker_model_provider'])
        conflict = self.observe_worker(prepared, self.lifecycle_observation(prepared, worker_id='another-child'))
        self.assertEqual(conflict['status'], 'uncertain')
        stored = evidence._read(self.scope, self.raw_assignment(prepared['attempt_id'])['observation'])
        self.assertEqual(stored['receipt']['worker_id'], 'child')
        self.assertEqual(evidence._read(self.scope, stored['conflict'])['receipt']['worker_id'], 'another-child')
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['capacity']['unresolved_managed_workers'], 1)

    def test_duplicate_uncertain_observation_keeps_original_response_and_history(self):
        from runtime.startup import StartupError
        self.plan()
        prepared = self.prepare()
        self.observe_worker(prepared)
        request = self.request(attempt_id=prepared['attempt_id'], parent_turn_id='parent-turn', spawn_item_id='spawn-item')
        conflicting = self.lifecycle_observation(prepared, worker_id='another-child')
        first = self.observe_worker(prepared, conflicting, request)
        duplicate = self.observe_worker(prepared, conflicting, request)
        self.assertTrue(duplicate['replayed'])
        self.assertEqual(duplicate['revision'], first['revision'])
        self.assertEqual(duplicate['observation_digest'], first['observation_digest'])
        request = self.request(attempt_id=prepared['attempt_id'], parent_turn_id='parent-turn', spawn_item_id='spawn-item')
        with patch('runtime.adapters.codex.observe_native_worker', side_effect=StartupError('fixture unavailable')):
            first = self.api.observe_assignment(self.scope, request)
            duplicate = self.api.observe_assignment(self.scope, request)
        self.assertTrue(duplicate['replayed'])
        self.assertEqual(duplicate['revision'], first['revision'])
        self.assertEqual(duplicate['observation_digest'], first['observation_digest'])

    def test_worker_lifecycle_guard_and_history_bound_preserve_unresolved_ownership(self):
        from runtime import changes
        self.plan()
        prepared = self.prepare()
        original = self.raw_assignment(prepared['attempt_id'])
        with changes._authority_guards([self.scope.runtime.state_dir]), self.assertRaises(state.StateError):
            self.observe_worker(prepared)
        self.assertEqual(self.raw_assignment(prepared['attempt_id']), original)
        with patch.object(self.api, 'MAX_OBSERVATIONS', 1):
            observed = self.lifecycle_observation(prepared, turn_status='inProgress')
            first = self.observe_worker(prepared, observed)
            retained = self.raw_assignment(prepared['attempt_id'])
            self.assertTrue(self.observe_worker(prepared, observed)['replayed'])
            with self.assertRaisesRegex(state.StateError, 'history bound'):
                self.observe_worker(prepared)
            self.assertEqual(self.raw_assignment(prepared['attempt_id']), retained)
        request = self.worker_reconciliation_request(prepared, outcome='cancelled')
        with changes._authority_guards([self.scope.runtime.state_dir]), self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, request)
        self.assertEqual(self.raw_assignment(prepared['attempt_id']), retained)
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['capacity']['unresolved_managed_workers'], 1)

    def test_same_turn_replacement_final_item_retains_conflict_after_valid_new_turn(self):
        import hashlib
        from runtime import evidence
        self.plan()
        prepared = self.prepare()
        self.observe_worker(prepared)
        next_turn = self.lifecycle_observation(prepared, worker_turn_id='next-turn', result_item_id='next-final')
        self.assertEqual(self.observe_worker(prepared, next_turn)['status'], 'observed')
        replacement = deepcopy(next_turn)
        report = json.loads(replacement['result'])
        report['limitations'] = ['Replacement final message contradicts immutable turn history']
        replacement['result'] = json.dumps(report)
        replacement['receipt']['result_item_id'] = 'replacement-final'
        replacement['receipt']['result_digest'] = hashlib.sha256(replacement['result'].encode()).hexdigest()
        result = self.observe_worker(prepared, replacement)
        self.assertEqual(result['status'], 'uncertain')
        data = self.raw_assignment(prepared['attempt_id'])
        retained = evidence._read(self.scope, data['observation'])
        self.assertEqual(retained['receipt']['result_item_id'], 'next-final')
        self.assertEqual(retained['receipt']['result_digest'], next_turn['receipt']['result_digest'])
        self.assertEqual(evidence._read(self.scope, retained['conflict'])['receipt']['result_item_id'], 'replacement-final')
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(prepared))
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['capacity']['unresolved_managed_workers'], 1)

    def test_new_turn_cannot_reuse_a_final_item_with_changed_content(self):
        import hashlib
        from runtime import evidence
        self.plan()
        prepared = self.prepare()
        original = self.lifecycle_observation(prepared)
        self.observe_worker(prepared, original)
        replacement = self.lifecycle_observation(prepared, worker_turn_id='new-turn-same-final-item')
        report = json.loads(replacement['result'])
        report['limitations'] = ['Changed content under the previously observed final-item identity']
        replacement['result'] = json.dumps(report)
        replacement['receipt']['result_digest'] = hashlib.sha256(replacement['result'].encode()).hexdigest()
        self.assertEqual(self.observe_worker(prepared, replacement)['status'], 'uncertain')
        retained = evidence._read(self.scope, self.raw_assignment(prepared['attempt_id'])['observation'])
        self.assertEqual(retained['receipt'], original['receipt'])
        self.assertEqual(evidence._read(self.scope, retained['conflict'])['receipt'], replacement['receipt'])
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(prepared))
        status = self.api.delegation_status(self.scope, 'codex', 'parent')
        self.assertEqual(status['capacity']['unresolved_managed_workers'], 1)
        self.assertEqual(status['budgets']['attempts_consumed'], 1)

    def test_foreign_reservation_during_capture_refuses_stale_candidate_settlement(self):
        from runtime import evidence
        (self.repo / 'other.py').write_text('before foreign worker\n')
        self.plan()
        prepared = self.prepare()
        self.observe_worker(prepared)
        foreign = self.second_owner(1, [self.path_claim('other.py')])
        request = self.worker_reconciliation_request(prepared)
        original = self.raw_assignment(prepared['attempt_id'])
        original_capture = evidence.capture_candidate
        foreign_admission = []
        def capture(scope, plan):
            candidate = original_capture(scope, plan)
            with patch.object(evidence, 'capture_candidate', side_effect=original_capture):
                foreign_admission.append(self.api.prepare_assignment(self.scope, foreign))
            (self.repo / 'other.py').write_text('changed by foreign admitted worker\n')
            return candidate
        with patch.object(evidence, 'capture_candidate', side_effect=capture):
            with self.assertRaisesRegex(state.StateError, 'reservation.*changed'):
                self.api.reconcile_assignment(self.scope, request)
        self.assertTrue(foreign_admission[0]['launch_admitted'])
        self.assertEqual(self.raw_assignment(prepared['attempt_id']), original)
        # Recapture/proof for the real candidate may settle the original worker; the foreign owner remains held.
        fresh = self.worker_reconciliation_request(prepared, changed_paths=['app.py', 'other.py'])
        self.assertEqual(self.api.reconcile_assignment(self.scope, fresh)['outcome'], 'completed')
        self.assertEqual(self.raw_assignment(foreign_admission[0]['attempt_id'])['status'], 'prepared')
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['capacity']['unresolved_managed_workers'], 1)
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 1)
        with state._database(self.scope) as db:
            self.assertEqual(len(self.api._ledger(self.scope, db)), 2)

    def test_replacement_cannot_forget_unresolved_worker_even_after_cancellation(self):
        self.plan()
        prepared = self.prepare()
        task = state.task_status(self.scope, 'codex', 'parent')['task']
        state.update_task(self.scope, self.identity | dict(expected_revision=task['revision'], state='CANCELLED',
            reason='Cancel intent retains ownership', reference='user:cancel'))
        current = state.task_status(self.scope, 'codex', 'parent')['task']
        with self.assertRaisesRegex(state.StateError, 'assignment'):
            state.start_task(self.scope, self.identity | dict(task_id='replacement', mode='editable', decision_ref='user:accepted',
                replaces=dict(task_id='task', expected_revision=current['revision'])))
        self.assertEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'prepared')

    def retained_d4(self, *, command=False, existing=False):
        """Actual retained D4 operation over original-schema data, never a canonical store loan."""
        from tests.test_changes import ChangesTests
        d4=ChangesTests('runTest');d4.setUp();self.addCleanup(d4.doCleanups)
        if existing:
            old,_,request=d4.existing_enrollment(runtime=True)
            d4.historical_runtime(old,'empty')
        else:
            request=d4.command_request("from pathlib import Path; Path('result.txt').write_text('result')") if command else d4.request()
        directory=d4.base/'retained-shared-state'
        record=request['manifest']['enrollment']['record']
        record['runtime']=dict(schema_version=2,state_dir=str(directory))
        d4.historical_runtime(record,'empty')
        request=d4.observe(d4.bind(request['manifest']))
        return d4,request,directory

    def assert_retained_guard_blocked(self, directory):
        from runtime import changes
        from runtime.startup import StartupError
        with self.assertRaises(StartupError):
            with changes._authority_guards([directory]):self.fail('Retained D4 guard unexpectedly released')

    def test_retained_d4_shared_authority_excludes_owner_after_scan(self):
        from runtime import changes
        d4,request,directory=self.retained_d4()
        before=d4.retained_inventory(directory)
        binding=dict(transaction_id=request['manifest']['transaction_id'],manifest_digest=request['approval']['manifest_digest'],
            manifest=request['manifest'],approval=request['approval'],plan=d4.plan())
        rules=d4.repo/'AGENTS.md'
        scanned,finish=threading.Event(),threading.Event()
        original=changes._guard_writers
        def scan(*args,**kwargs):
            original(*args,**kwargs)
            if not scanned.is_set():
                scanned.set();self.assertTrue(finish.wait(timeout=10))
        with patch.object(changes,'_guard_writers',side_effect=scan):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future=executor.submit(changes.apply_plan,d4.directory,request)
                try:
                    self.assertTrue(scanned.wait(timeout=5),'Actual retained D4 writer guard was not reached')
                    self.assert_retained_guard_blocked(directory)
                finally:finish.set()
                result=future.result(timeout=10)
        self.assertEqual(result['exit_code'],2,result)
        self.assertEqual(result['completed'],['write-rules'],result)
        self.assertEqual(result['blocked'],[dict(id='transaction',
            reason='Collect adoption qualification/reviews and bind observations to current inputs before final enrollment')],result)
        self.assertEqual((rules.read_bytes(),rules.stat().st_mode&0o777),(b'product rules\nnew process\n',0o644))
        self.assertFalse((d4.repo/changes.PROJECT_FILE).exists())
        self.assertIsNone(d4.journal()['enrollment']['backup'])
        self.assertEqual({key:d4.journal()[key] for key in binding},binding)
        self.assertEqual(before,d4.retained_inventory(directory))
        result=changes.resume_plan(d4.directory,d4.observe(request))
        d4.assert_activation_incompatible(result)
        self.assertEqual(result['blocked'],[dict(id='transaction',
            reason='Enrollment runtime state/pins are incompatible; reconcile them before activation')],result)
        self.assertEqual({key:d4.journal()[key] for key in binding},binding)
        self.assertEqual(before,d4.retained_inventory(directory))
        self.assertEqual((rules.read_bytes(),rules.stat().st_mode&0o777),(b'product rules\nnew process\n',0o644))

    def test_uncertain_retained_d4_command_owner_holds_authority_until_reconciled(self):
        from runtime import changes
        d4,request,directory=self.retained_d4(command=True)
        before=d4.retained_inventory(directory)
        result=changes.apply_plan(d4.directory,request)
        self.assertEqual(result['exit_code'],2,result)
        self.assertFalse(d4.journal()['receipts']['command']['command']['stopped'])
        self.assert_retained_guard_blocked(directory)
        result=changes.resume_plan(d4.directory,d4.observe(request))
        d4.assert_activation_incompatible(result)
        self.assertTrue(d4.journal()['receipts']['command']['command']['stopped'])
        with changes._authority_guards([directory]):pass
        self.assertEqual(before,d4.retained_inventory(directory))

    def test_existing_retained_foreign_assignment_blocks_d4_before_mutation(self):
        from runtime import changes
        from contextlib import closing
        d4,request,directory=self.retained_d4()
        # Unsupported original assignment history is a protected unknown owner.
        with closing(sqlite3.connect(directory/'runtime.sqlite3')) as db,db:
            db.execute('insert into assignments values(?,?,?,?,?,?,?)',('f'*32,'original-foreign-scope','codex',
                'original-session','original-task','original-dispatch','{}'))
        before=d4.retained_inventory(directory)
        rules=(d4.repo/'AGENTS.md').read_bytes()
        reached=[];original=changes._writers
        def inspect(*args,**kwargs):reached.append(True);return original(*args,**kwargs)
        with patch.object(changes,'_writers',side_effect=inspect):result=changes.apply_plan(d4.directory,request)
        self.assertTrue(reached)
        self.assertEqual(result['exit_code'],2,result)
        self.assertIn('assignment history',result['blocked'][0]['reason'])
        self.assertEqual((d4.repo/'AGENTS.md').read_bytes(),rules)
        self.assertEqual(before,d4.retained_inventory(directory))

    def test_retained_d4_rollback_guards_proposed_actual_and_backup_authorities(self):
        from runtime import changes
        d4,request,directory=self.retained_d4(existing=True)
        old_directory=d4.base/'task-state'
        before={path:d4.retained_inventory(path) for path in (old_directory,directory)}
        pointer=d4.repo/changes.PROJECT_FILE;original_enrollment=pointer.read_bytes()
        rules=d4.repo/'AGENTS.md';original_rules=(rules.read_bytes(),rules.stat().st_mode&0o777)
        binding=dict(transaction_id=request['manifest']['transaction_id'],manifest_digest=request['approval']['manifest_digest'],
            manifest=request['manifest'],approval=request['approval'],plan=d4.plan())
        result=changes.apply_plan(d4.directory,request)
        self.assertEqual(result['exit_code'],2,result)
        self.assertEqual(result['completed'],['write-rules'],result)
        self.assertEqual(result['blocked'],[dict(id='transaction',
            reason='Collect adoption qualification/reviews and bind observations to current inputs before final enrollment')],result)
        self.assertEqual((rules.read_bytes(),rules.stat().st_mode&0o777),(b'product rules\nnew process\n',0o644))
        self.assertFalse(pointer.exists())
        self.assertEqual((d4.directory/d4.journal()['enrollment']['backup']).read_bytes(),original_enrollment)
        self.assertEqual({key:d4.journal()[key] for key in binding},binding)
        for path in before:self.assertEqual(before[path],d4.retained_inventory(path))
        result=changes.resume_plan(d4.directory,d4.observe(request))
        d4.assert_activation_incompatible(result)
        self.assertEqual(result['blocked'],[dict(id='transaction',
            reason='Enrollment runtime state/pins are incompatible; reconcile them before activation')],result)
        seen=[];held=[];original=changes._guard_writers;original_writers=changes._writers
        def inspect(*args,**kwargs):
            result=original_writers(*args,**kwargs)
            if kwargs.get('scope') is not None:
                self.assertTrue(changes._held_authority_guards([old_directory,directory]))
                held.append(kwargs['scope'].runtime.state_dir)
            return result
        def scan(*args,**kwargs):
            original(*args,**kwargs)
            for path in (old_directory,directory):
                with self.assertRaisesRegex(changes.Pending,
                        '^Shared state authority has an active or uncertain D4 operation; resume its bound journal$'):
                    with changes._authority_guards([path]):self.fail('Retained rollback authority unexpectedly released')
                seen.append(path)
        with patch.object(changes,'_writers',side_effect=inspect),patch.object(changes,'_guard_writers',side_effect=scan):
            result=changes.rollback_plan(d4.directory,d4.observe(request))
            self.assertEqual(set(held),{old_directory,directory})
            self.assertEqual(set(seen),{old_directory,directory})
            self.assertEqual(result['exit_code'],2,result)
            self.assertEqual(result['blocked'],[dict(id='enrollment',
                reason='Prior enrollment stays inactive until client/runtime compatibility is qualified')],result)
            self.assertEqual(d4.journal()['receipts']['write-rules']['status'],'rolled_back')
            self.assertEqual((rules.read_bytes(),rules.stat().st_mode&0o777),original_rules)
            self.assertFalse(pointer.exists())
            self.assertEqual((d4.directory/d4.journal()['enrollment']['backup']).read_bytes(),original_enrollment)
            self.assertEqual({key:d4.journal()[key] for key in binding},binding)
            for path in before:self.assertEqual(before[path],d4.retained_inventory(path))
            result=changes.rollback_plan(d4.directory,d4.observe(request))
        self.assertEqual(set(held),{old_directory,directory})
        self.assertEqual(set(seen),{old_directory,directory})
        self.assertEqual(result['exit_code'],2,result)
        self.assertEqual(result['blocked'],[dict(id='enrollment',
            reason='Prior enrollment is stale or incompatible and remains inactive')],result)
        self.assertFalse(pointer.exists())
        self.assertEqual((rules.read_bytes(),rules.stat().st_mode&0o777),original_rules)
        self.assertEqual((d4.directory/d4.journal()['enrollment']['backup']).read_bytes(),original_enrollment)
        self.assertEqual({key:d4.journal()[key] for key in binding},binding)
        for path in before:self.assertEqual(before[path],d4.retained_inventory(path))

    def test_missing_retained_d4_journal_does_not_release_uncertain_owner(self):
        from runtime import changes
        d4,request,directory=self.retained_d4(command=True)
        before=d4.retained_inventory(directory)
        self.assertEqual(changes.apply_plan(d4.directory,request)['exit_code'],2)
        journal=d4.directory/'transaction.json';saved=journal.read_bytes();journal.unlink()
        self.assert_retained_guard_blocked(directory)
        self.assertEqual(changes.resume_plan(d4.directory,request)['exit_code'],1)
        self.assert_retained_guard_blocked(directory)
        journal.write_bytes(saved);journal.chmod(0o600)
        d4.assert_activation_incompatible(changes.resume_plan(d4.directory,d4.observe(request)))
        with changes._authority_guards([directory]):pass
        self.assertEqual(before,d4.retained_inventory(directory))

    def assert_git_routing_change_is_rejected_at_commit(self, change):
        from runtime import evidence
        linked = self.repo
        private = Path(subprocess.check_output(['git', '-C', str(linked), 'rev-parse',
            '--path-format=absolute', '--git-dir'], text=True).strip())
        spare = self.repo / 'spare-private'
        shutil.copytree(private, spare)
        (spare / 'commondir').write_text(str(self.scope.git_common_dir) + '\n')
        scope = self.scope
        target = spare / 'HEAD'
        alias = self.root / 'routing-alias'
        if change == 'private-alias':
            alias.symlink_to(private, target_is_directory=True)
            (linked / '.git').write_text('gitdir: ' + str(alias) + '\n')
        elif change == 'common-alias':
            alias.symlink_to(self.scope.git_common_dir, target_is_directory=True)
            (private / 'commondir').write_text(str(alias) + '\n')
            spare_common = self.repo / 'spare-common'
            shutil.copytree(self.scope.git_common_dir, spare_common)
            target = spare_common / 'config'
        request = self.second_owner(1, [dict(kind='path', key=str(target), mode='write')], scope=scope)
        mutated = []
        def mutate():
            mutated.append(change)
            if change == 'marker':
                (linked / '.git').write_text('gitdir: ' + str(spare) + '\n')
            elif change == 'private-alias':
                alias.unlink()
                alias.symlink_to(spare, target_is_directory=True)
            elif change == 'commondir-bytes':
                (private / 'commondir').write_text(str(self.scope.git_common_dir) + '\n')
            else:
                alias.unlink()
                alias.symlink_to(spare_common, target_is_directory=True)
        if change == 'common-alias':
            original = evidence._artifact
            changed = False
            def publish(artifact_scope, value, **kwargs):
                nonlocal changed
                if artifact_scope == scope and not changed:
                    changed = True
                    mutate()
                return original(artifact_scope, value, **kwargs)
            interception = patch.object(evidence, '_artifact', side_effect=publish)
        else:
            original = self.discover.side_effect
            def discover(owner, controls):
                mutate()
                return original(owner, controls)
            interception = patch.object(self.discover, 'side_effect', discover)
        with interception:
            with self.assertRaises(state.StateError):
                self.api.prepare_assignment(scope, request)
        self.assertTrue(mutated, 'The intended late Git routing mutation was not reached')
        with state._database(scope) as db:
            self.assertEqual(db.execute('select count(*) from assignments').fetchone()[0], 0)
            task = state._task(db, state._scope_id(scope), request['client'], request['session_id'], request['task_id'])
            self.assertEqual(task['revision'], request['expected_revision'])
            self.assertEqual(task['delegation']['attempts_consumed'], 0)
            self.assertEqual(task['delegation']['resource_units_consumed'], 0)

    def test_git_marker_retarget_during_discovery_blocks_commit(self):
        self.assert_git_routing_change_is_rejected_at_commit('marker')

    def test_git_private_alias_retarget_with_unchanged_marker_blocks_commit(self):
        self.assert_git_routing_change_is_rejected_at_commit('private-alias')

    def test_git_commondir_byte_change_during_discovery_blocks_commit(self):
        self.assert_git_routing_change_is_rejected_at_commit('commondir-bytes')

    def test_git_common_alias_retarget_after_candidate_capture_blocks_commit(self):
        self.assert_git_routing_change_is_rejected_at_commit('common-alias')

    def test_unchanged_git_routing_admits_and_replay_skips_git_and_native_observation(self):
        from runtime import evidence
        self.plan()
        request = self.prepare_request()
        first = self.api.prepare_assignment(self.scope, request)
        self.assertTrue(first['launch_admitted'])
        with patch.object(evidence, '_git', side_effect=AssertionError('replay invoked Git')):
            with patch('runtime.adapters.codex.discover_native_workers', side_effect=AssertionError('replay invoked native discovery')):
                replay = self.api.prepare_assignment(self.scope, request)
        self.assertEqual(first['attempt_id'], replay['attempt_id'])
        self.assertFalse(replay['launch_admitted'])
        self.assertTrue(replay['replayed'])

    def test_git_metadata_observation_is_outside_database_transactions(self):
        from runtime import evidence
        original = evidence._git
        observed = []
        def read(root, *arguments):
            if '--git-dir' in arguments:
                with state._database(self.scope, write=True):
                    pass
                observed.append(root)
            return original(root, *arguments)
        with patch.object(evidence, '_git', side_effect=read):
            self.plan()
            self.assertTrue(self.prepare()['launch_admitted'])
        self.assertEqual(len(observed), 2)

    def test_descendant_claim_cannot_cover_a_named_parent_file(self):
        value = self.plan_value()
        value['jobs'][0]['claims'] = [self.path_claim('new.py/child')]
        self.plan(value)
        request = self.prepare_request()
        request['brief'].update(resources=value['jobs'][0]['claims'], files=['new.py'])
        with self.assertRaisesRegex(state.StateError, 'unreserved file'):
            self.api.prepare_assignment(self.scope, request)

    def test_status_and_admission_both_detect_retargeted_held_path(self):
        self.plan()
        first = self.prepare()
        (self.repo / 'other.py').write_text('other = 1\n')
        other = self.second_owner(1, [self.path_claim('other.py')])
        (self.repo / 'app.py').rename(self.repo / 'saved.py')
        (self.repo / 'app.py').symlink_to(self.repo / 'other.py')
        report = self.api.delegation_status(self.scope, 'codex', other['session_id'])
        self.assertEqual([c['attempt_id'] for c in report['conflicts']], [first['attempt_id']])
        with self.assertRaisesRegex(state.StateError, 'Canonical resource ownership conflicts'):
            self.api.prepare_assignment(self.scope, other)
        (self.repo / 'app.py').unlink()
        (self.repo / 'app.py').symlink_to('app.py')
        with self.assertRaises(state.StateError):
            self.api.delegation_status(self.scope, 'codex', other['session_id'])

    def test_directional_file_coverage_preserves_exact_hardlink_and_symlink_aliases(self):
        os.link(self.repo / 'app.py', self.repo / 'hard.py')
        (self.repo / 'sym.py').symlink_to(self.repo / 'app.py')
        (self.repo / '.git/info/exclude').write_text('sym.py\n')
        self.plan()
        request = self.prepare_request()
        request['brief']['files'] = ['hard.py', 'sym.py']
        self.assertTrue(self.api.prepare_assignment(self.scope, request)['launch_admitted'])

    def test_directory_file_coverage_rejects_escaping_symlink(self):
        (self.repo / 'work').mkdir()
        outside = self.repo / 'outside.py'
        outside.write_text('outside = 1\n')
        (self.repo / 'work/link.py').symlink_to(outside)
        (self.repo / '.git/info/exclude').write_text('work/link.py\n')
        value = self.plan_value()
        value['jobs'][0]['claims'] = [self.path_claim('work')]
        self.plan(value)
        request = self.prepare_request()
        request['brief'].update(resources=value['jobs'][0]['claims'], files=['work/link.py'])
        with self.assertRaisesRegex(state.StateError, 'unreserved file'):
            self.api.prepare_assignment(self.scope, request)

    def test_mutating_caller_input_cannot_change_the_validated_immutable_plan_or_brief(self):
        from runtime import evidence
        value = self.plan_value()
        original_artifact = evidence._artifact
        def publish(scope, content, **kwargs):
            value['limits']['max_attempts'] = 9
            return original_artifact(scope, content, **kwargs)
        with patch.object(evidence, '_artifact', side_effect=publish):
            planned = self.plan(value)
        self.assertEqual(evidence._read(self.scope, planned['plan'])['limits']['max_attempts'], 8)
        request = self.prepare_request()
        frozen_request = deepcopy(request)
        original_discovery = self.discover.side_effect
        def discover(scope, controls):
            request['brief']['files'] = ['../unreserved.py']
            return original_discovery(scope, controls)
        self.discover.side_effect = discover
        result = self.api.prepare_assignment(self.scope, request)
        body = evidence._read(self.scope, self.raw_assignment(result['attempt_id'])['assignment'])
        self.assertEqual(body['request']['brief']['files'], ['app.py'])
        self.assertFalse(self.api.prepare_assignment(self.scope, frozen_request)['launch_admitted'])

    def test_unsupported_explicit_selection_requires_documented_suitable_inheritance(self):
        self.catalog['dispatch_controls'].update(explicit_model=False, explicit_effort=False)
        self.catalog['parent_model_provider'] = None
        self.plan()
        request = self.prepare_request()
        with self.assertRaisesRegex(state.StateError, 'unsupported by exposed controls'):
            self.api.prepare_assignment(self.scope, request)
        request['selection']['mode'] = 'inherit'
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, request)
        request['selection']['inheritance'] = dict(model='new-native-model', effort='high',
            reference='coordinator:verified-current-native-inheritance')
        result = self.api.prepare_assignment(self.scope, request)
        binding = self.api.prepared_native_assignment(self.scope, result['attempt_id'])
        self.assertIsNone(binding['requested_model'])
        self.assertIsNone(binding['requested_effort'])
        from runtime import evidence
        assignment = evidence._read(self.scope, self.raw_assignment(result['attempt_id'])['assignment'])
        self.assertIsNone(evidence._read(self.scope, assignment['discovery'])['parent_model_provider'])

    def test_shared_port_keys_normalize_protocol_case_and_numeric_aliases(self):
        first_claim = dict(kind='port', host='local', key='TCP:0049', mode='write')
        value = self.plan_value()
        value['jobs'][0]['claims'] = [first_claim]
        self.plan(value)
        request = self.prepare_request()
        request['brief'].update(files=[], resources=[first_claim])
        self.api.prepare_assignment(self.scope, request)
        other = self.second_owner(1, [dict(kind='port', host='local', key='tcp:49', mode='write')])
        with self.assertRaisesRegex(state.StateError, 'Canonical resource ownership conflicts'):
            self.api.prepare_assignment(self.scope, other)

    def test_status_reports_conflicting_owners_across_sessions(self):
        self.plan()
        first = self.prepare()
        other = self.second_owner(1)
        report = self.api.delegation_status(self.scope, 'codex', other['session_id'])
        self.assertIn('conflicts', report)
        self.assertEqual(report['conflicts'][0]['attempt_id'], first['attempt_id'])
        self.assertEqual(report['conflicts'][0]['owner']['session_id'], 'parent')

    def test_uncertain_launch_retains_capacity_and_never_reissues_permission(self):
        self.plan()
        request = self.prepare_request()
        first = self.api.prepare_assignment(self.scope, request)
        with state._database(self.scope, write=True) as db:
            data = self.raw_assignment(first['attempt_id'])
            data['status'] = 'uncertain'
            db.execute('update assignments set data=? where id=?', (json.dumps(data), first['attempt_id']))
        self.discover.reset_mock()
        replay = self.api.prepare_assignment(self.scope, request)
        self.assertFalse(replay['launch_admitted'])
        self.assertEqual(replay['status'], 'uncertain')
        self.assertEqual(self.discover.call_count, 0)
        other = self.second_owner(1, [self.path_claim('independent.py')], dict(max_parallel_workers=1, host_capacity=1))
        with self.assertRaisesRegex(state.StateError, 'capacity is exhausted'):
            self.api.prepare_assignment(self.scope, other)

    def test_retry_needs_new_evidence_and_preserves_per_job_budget(self):
        self.plan()
        first = self.prepare()
        self.settle_fixture(first['attempt_id'])
        retry = self.prepare_request(dispatch_id='retry-1', retry_of=first['attempt_id'], diagnostic=dict(
            prior_attempt_id=first['attempt_id'], cause='Diagnosed failure', evidence=['host:first-new-evidence'],
            change='Correct bounded input'))
        second = self.api.prepare_assignment(self.scope, retry)
        self.settle_fixture(second['attempt_id'])
        retry.update(dispatch_id='retry-2', retry_of=second['attempt_id'], expected_revision=self.request()['expected_revision'])
        retry['diagnostic']['prior_attempt_id'] = second['attempt_id']
        with self.assertRaisesRegex(state.StateError, 'new diagnostic evidence'):
            self.api.prepare_assignment(self.scope, retry)
        retry['diagnostic']['evidence'] = ['host:second-new-evidence']
        third = self.api.prepare_assignment(self.scope, retry)
        self.settle_fixture(third['attempt_id'])
        retry.update(dispatch_id='retry-3', retry_of=third['attempt_id'], expected_revision=self.request()['expected_revision'])
        retry['diagnostic'].update(prior_attempt_id=third['attempt_id'], evidence=['host:third-new-evidence'])
        with self.assertRaisesRegex(state.StateError, 'job attempt budget'):
            self.api.prepare_assignment(self.scope, retry)

    def test_unknown_old_check_resources_in_another_checkout_block_dispatch(self):
        from runtime import evidence
        self.plan()
        other_scope = self.peer_scope
        linked = other_scope.repository_root
        other = self.second_owner(1, [dict(kind='path', key=str(linked / 'app.py'), mode='read')], scope=other_scope)
        identity = {key: other[key] for key in ('client', 'session_id', 'task_id', 'scope_ref', 'expected_revision')}
        self.legacy_unlinked_review_fixture(other_scope, identity)
        with self.assertRaisesRegex(state.StateError, 'Unreconciled D5 attempt'):
            self.prepare()

    def test_worker_cannot_drop_an_applicable_accepted_check(self):
        from runtime import evidence
        proof = evidence._load_plan(self.scope, self.identity)
        proof['checks'].append(deepcopy(proof['checks'][0]) | dict(id='unrelated', dependencies=['unused.py']))
        evidence.plan_evidence(self.scope, self.request(plan=proof, change_decision_ref='user:checks-refined'))
        self.plan()
        request = self.prepare_request()
        request['brief']['checks'] = ['unrelated']
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, request)

    def test_lost_response_cannot_mint_another_launch_permission(self):
        self.plan()
        request = self.prepare_request()
        with patch.object(self.api, '_reply', side_effect=RuntimeError('response lost after committed reservation')):
            with self.assertRaises(RuntimeError):
                self.api.prepare_assignment(self.scope, request)
        self.discover.reset_mock()
        replay = self.api.prepare_assignment(self.scope, request)
        self.assertFalse(replay['launch_admitted'])
        self.assertTrue(replay['replayed'])
        self.assertEqual(self.discover.call_count, 0)
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 1)

    def test_failure_before_native_commit_rolls_back_without_spending_launch(self):
        self.plan()
        request = self.prepare_request()
        with state._database(self.scope) as db:
            before = deepcopy(state._task(db, state._scope_id(self.scope), 'codex', 'parent'))
        with patch.object(state, '_save', side_effect=state.StateError('fixture failure before commit')), \
             patch.object(self.api, '_reply', side_effect=AssertionError('response before durable reservation')):
            with self.assertRaisesRegex(state.StateError, 'fixture failure before commit'):
                self.api.prepare_assignment(self.scope, request)
        with state._database(self.scope) as db:
            self.assertEqual(self.api._ledger(self.scope, db), [])
            self.assertEqual(state._task(db, state._scope_id(self.scope), 'codex', 'parent'), before)
        admitted = self.api.prepare_assignment(self.scope, request)
        self.assertTrue(admitted['launch_admitted'])
        self.assertFalse(admitted['replayed'])
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 1)

    def test_shared_reads_coexist_and_write_claims_conflict(self):
        value = self.plan_value()
        value['jobs'][0]['claims'] = [self.path_claim(mode='read')]
        self.plan(value)
        request = self.prepare_request()
        request['brief']['resources'] = value['jobs'][0]['claims']
        self.api.prepare_assignment(self.scope, request)
        read = self.second_owner(1, [self.path_claim(mode='read')])
        self.assertTrue(self.api.prepare_assignment(self.scope, read)['launch_admitted'])
        write = self.second_owner(2)
        with self.assertRaisesRegex(state.StateError, 'Canonical resource ownership conflicts'):
            self.api.prepare_assignment(self.scope, write)

    def test_capacity_race_across_disjoint_sessions_admits_only_one(self):
        requests = [self.second_owner(n, [self.path_claim('file-' + str(n) + '.py')],
                    dict(max_parallel_workers=1, host_capacity=1)) for n in range(2)]
        barrier = threading.Barrier(2)
        def run(request):
            barrier.wait(timeout=5)
            try:
                return self.api.prepare_assignment(self.scope, request)['launch_admitted']
            except state.StateError:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sum(executor.map(run, requests)), 1)

    def test_unknown_fields_brief_scope_and_coordinator_resources_are_rejected(self):
        value = self.plan_value()
        git_job = deepcopy(value)
        git_job['jobs'][0]['claims'] = [dict(kind='git', key=str(self.repo / '.git'), mode='write')]
        with self.assertRaises(state.StateError):
            self.plan(git_job)
        self.plan(value)
        request = self.prepare_request()
        for delta in (dict(secret='forbidden'), dict(brief=request['brief'] | dict(resources=[])),
                      dict(brief=request['brief'] | dict(checks=[])), dict(brief=request['brief'] | dict(files=['../outside']))):
            with self.assertRaises(state.StateError):
                self.api.prepare_assignment(self.scope, request | delta)


    def test_v1_receipt_observation_keeps_writer_ownership_until_independent_stop_proof(self):
        self.plan()
        prepared = self.prepare()
        binding = self.api.prepared_native_assignment(self.scope, prepared['attempt_id'])
        observed = self.lifecycle_observation(prepared, mechanism='codex-native-collab-call',
            agent_path='/actual/' + binding['task_name'])
        self.assertEqual(self.observe_worker(prepared, observed)['status'], 'observed')
        self.assertTrue(self.observe_worker(prepared, observed)['replayed'])
        # Keep the same valid path to isolate a mechanism-only identity conflict.
        route = deepcopy(observed); route['receipt']['mechanism'] = 'codex-native-agent-path'
        self.assertEqual(self.observe_worker(prepared, route)['status'], 'uncertain')
        first_conflict = evidence._read(self.scope, self.raw_assignment(prepared['attempt_id'])['observation'])['conflict']
        child = deepcopy(observed); child['receipt']['worker_id'] = 'different-child'
        self.assertEqual(self.observe_worker(prepared, child)['status'], 'uncertain')
        latest = evidence._read(self.scope, self.raw_assignment(prepared['attempt_id'])['observation'])
        self.assertNotEqual(first_conflict, latest['conflict'])
        self.assertEqual(latest['receipt']['worker_id'], 'child')
        for flag in (None, 'stopped', 'descendants_stopped'):
            request = self.worker_reconciliation_request(prepared, outcome='failed', changed_paths=[],
                proof_delta={flag: False} if flag else None)
            if flag is None:
                request.pop('host_proof')
            with self.subTest(flag=flag), self.assertRaises(state.StateError):
                self.api.reconcile_assignment(self.scope, request)
            self.assertNotEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'settled')
            with self.assertRaises(state.StateError):
                self.api.reserve_coordinator(self.scope, self.operation_request(claims=[self.path_claim()]))
        # Fixture host proof models independently inspected stopping, not native qualification.
        exact = self.worker_reconciliation_request(prepared, outcome='failed', changed_paths=[])
        self.assertEqual(self.api.reconcile_assignment(self.scope, exact)['status'], 'settled')
        self.assertTrue(self.api.reconcile_assignment(self.scope, exact)['replayed'])
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['capacity']['unresolved_managed_workers'], 0)

    def test_native_receipt_keeps_admitted_version_when_observer_updates(self):
        self.plan()
        prepared = self.prepare()
        self.observe_worker(prepared)
        observed = self.lifecycle_observation(prepared, current_observer_version='0.165.0',
            parent_creation_version='0.164.0-alpha.11.1', worker_creation_version='0.164.0-alpha.11.1')
        self.assertEqual(self.observe_worker(prepared, observed)['status'], 'observed')
        with state._database(self.scope) as db:
            record = next(row for row in self.api._ledger(self.scope, db) if row['id'] == prepared['attempt_id'])
        receipt = self.api._worker_history(self.scope, record['data']['observation'])[0][1]['receipt']
        self.assertEqual(receipt['native_version'], '0.163.0')
        self.assertEqual(receipt['current_observer_version'], '0.165.0')
        self.assertEqual(record['data']['binding']['native_version'], '0.163.0')
        request = self.request(attempt_id=prepared['attempt_id'], parent_turn_id='parent-turn', spawn_item_id='spawn-item')
        for key in ('current_observer_version', 'parent_creation_version', 'worker_creation_version'):
            bad = dict(observed, receipt=observed['receipt'] | {key: None})
            with self.subTest(key=key), self.assertRaises(state.StateError):
                self.api._worker_receipt(self.scope, record, request, bad)


    def test_admission_accepts_compatible_reader_update_and_records_actual_provenance(self):
        self.plan()
        request = self.prepare_request()
        request['control_observation']['native_version'] = '0.100.0'
        request['discovery_expectation']['native_version'] = '0.120.0'
        self.catalog['native_version'] = '9.999.0'
        prepared = self.api.prepare_assignment(self.scope, request)
        self.assertTrue(prepared['launch_admitted'])
        binding = self.api.prepared_native_assignment(self.scope, prepared['attempt_id'])
        self.assertEqual(binding['native_version'], '9.999.0')
        data = self.raw_assignment(prepared['attempt_id'])
        assignment = evidence._read(self.scope, data['assignment'])
        self.assertEqual(evidence._read(self.scope, assignment['discovery'])['native_version'], '9.999.0')
        self.assertEqual(assignment['request']['control_observation']['native_version'], '0.100.0')
        self.assertEqual(assignment['request']['discovery_expectation']['native_version'], '0.120.0')


class Stage3ModelFreeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_evidence.EvidenceTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.repo = self.fixture.root, self.fixture.repo
        self.scope, self.identity = self.fixture.scope, self.fixture.identity
        for target in (patch.object(delegation, 'guidance_status', side_effect=AssertionError('model-free guidance lookup')),
                patch('runtime.adapters.codex.discover_native_workers', side_effect=AssertionError('model-free native discovery'))):
            target.start()
            self.addCleanup(target.stop)
        self.counter = self.root / 'check-runs.txt'
        self.counter.write_text('')

    def request(self, **fields):
        return self.fixture.request(**fields)

    def task(self):
        with state._database(self.scope) as db:
            return deepcopy(state._task(db, state._scope_id(self.scope), self.identity['client'],
                self.identity['session_id'], self.identity['task_id']))

    def records(self):
        with state._database(self.scope) as db:
            return delegation._ledger(self.scope, db)

    def record(self, attempt_id):
        found = [row for row in self.records() if row['id'] == attempt_id]
        self.assertEqual(len(found), 1, 'Direct D5 attempt must have exactly one same-ID ledger row')
        return found[0]

    def check_plan(self, code='pass', *, mutable=False):
        self.fixture.plan()
        plan = evidence._load_plan(self.scope, self.identity)
        check = plan['checks'][0]
        check['argv'] = [sys.executable, '-c', code]
        if mutable:
            check['effects']['kind'] = 'disposable'
            check['resources'] = [dict(kind='path', key=str(self.counter), mode='write')]
        try:
            evidence.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:fixture-command'))
        except state.StateError as error:
            if mutable and str(error) == 'Invalid task control fields':
                self.fail('Stage3 declared check.resources admission is absent: ' + str(error))
            raise
        return plan

    def operation_request(self, **fields):
        return self.request(dispatch_id='operation-1',
            claims=[dict(kind='path', key=str(self.root / 'coordinator.txt'), mode='write')],
            operation=dict(id='operation-1', kind='coordinator-work'),
            reason='Complete one accepted disposable fixture operation',
            ownership_ref='fixture:authorized-resource-owner', stop_conditions=['Stop after writing the fixture']) | fields

    def reserve_coordinator(self, request):
        public = getattr(delegation, 'reserve_coordinator', None)
        self.assertTrue(callable(public), 'Stage3 public reserve_coordinator is absent; do not substitute the private kernel')
        with patch.object(evidence, 'capture_candidate', side_effect=AssertionError('task-only candidate capture')):
            return public(self.scope, request)

    def private_proof(self, proof):
        raw = json.dumps(proof).encode()
        path = self.root / ('host-' + hashlib.sha256(raw).hexdigest() + '.json')
        if not path.exists():
            path.write_bytes(raw)
            path.chmod(0o400)
        else:
            self.assertEqual(path.read_bytes(), raw)
        return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest())

    def coordinator_proof(self, attempt_id, *, write_output=True):
        row, task = self.record(attempt_id), self.task()
        operation = evidence._read(self.scope, row['body']['operation'])
        output = self.root / 'coordinator.txt'
        if write_output:
            output.write_text('accepted operation completed\n')
        bindings = dict(operation_id=operation['id'], operation_digest=row['body']['operation']['digest'],
            claims_digest=evidence._digest(row['data']['claims']))
        observation = ('Fixture synchronous write returned; no descendants; output SHA-256 ' +
            hashlib.sha256(output.read_bytes()).hexdigest()) if write_output else 'Fixture verifier/Git operation and descendants independently inspected stopped'
        observed = fixture_artifact(self.scope,self.identity, bindings | dict(observation=observation))
        proof = dict(version=2, kind='coordinator-operation-reconciliation', identity=self.identity.copy(),
            expected_pins=task['pins'], expected_revision=task['revision'], scope_id=state._scope_id(self.scope),
            attempt_id=attempt_id, dispatch_id=row['owner']['dispatch_id'], assignment_digest=row['data']['assignment']['digest'],
            decision_ref=task['decision_ref'], operation_evidence=observed, resource_observations=observed,
            operation_stopped=True, operation_descendants_stopped=True,
            observation_ref='fixture:completed-synchronous-operation', coordinator_ref='fixture:independent-file-inspection',
            **bindings)
        for key in ('d5_plan', 'd6_plan'):
            if row['body'][key] is not None:
                proof[key + '_digest'] = row['body'][key]['digest']
        if row['data']['candidate'] is not None:
            proof['candidate_digest'] = row['data']['candidate']['digest']
            proof['current_candidate_digest'] = evidence._digest(evidence.capture_candidate(self.scope,
                evidence._read(self.scope, row['body']['d5_plan'])))
        return proof

    def reconcile_operation(self, proof):
        request = self.request(attempt_id=proof['attempt_id'],
            host_proof=self.private_proof(proof), assessment=dict(outcome='completed', changed_paths=[],
                result_assessment='Observed the exact accepted fixture output',
                partial_change_assessment='Only the declared private file changed; synchronous operation returned without descendants',
                evidence=['fixture:independent-file-inspection'], coordinator_ref='fixture:assessed'))
        if self.record(proof['attempt_id'])['data']['candidate'] is None:
            with patch.object(evidence, 'capture_candidate', side_effect=AssertionError('task-only candidate capture')):
                return delegation.reconcile_assignment(self.scope, request)
        return delegation.reconcile_assignment(self.scope, request)

    def test_explicit_check_replay_does_not_repeat_command_or_add_attempt(self):
        # Catches a replay lookup that happens after subprocess execution/reservation.
        code = "from pathlib import Path; p=Path(%r); p.write_text(p.read_text() + 'run\\n')" % str(self.counter)
        self.check_plan(code, mutable=True)
        request = self.request(check_id='unit', dispatch_id='stable-check-1')
        first = evidence.run_check(self.scope, request)
        original, original_rows = self.task(), self.records()
        replay = evidence.run_check(self.scope, request)
        self.assertEqual(self.counter.read_text(), 'run\n')
        self.assertEqual(replay['attempt_id'], first['attempt_id'])
        self.assertEqual(replay['attempt'], first['attempt'])
        self.assertEqual(self.task(), original)
        self.assertEqual(self.records(), original_rows)
        self.assertEqual(len(original['evidence']['attempts']), 1)
        self.record(first['attempt_id'])

    def test_original_legacy_check_request_replays_without_a_new_attempt(self):
        # Catches stale-CAS rejection before deterministic replay for existing callers.
        self.check_plan('import os; print(os.getpid())')
        request = self.request(check_id='unit')
        first = evidence.run_check(self.scope, request)
        original = self.task()
        try:
            replay = evidence.run_check(self.scope, request)
        except state.StateError as error:
            if str(error) == 'Task revision conflict':
                self.fail('Stage3 deterministic legacy replay is absent: ' + str(error))
            raise
        self.assertEqual(replay['attempt_id'], first['attempt_id'])
        self.assertEqual(replay['attempt']['execution'], first['attempt']['execution'])
        self.assertEqual(self.task(), original)
        self.assertEqual(len(original['evidence']['attempts']), 1)

    def test_model_free_check_links_and_settles_actual_failure_without_native_budget(self):
        # Catches invented native admission and loss of actual exit/output during settlement.
        self.check_plan("print('fixture failed'); raise SystemExit(7)")
        result = evidence.run_check(self.scope, self.request(check_id='unit'))
        attempt, task = result['attempt'], self.task()
        self.assertEqual(attempt['exit_code'], 7)
        self.assertEqual(attempt['observed_status'], 'failed')
        self.assertNotIn('delegation', task)
        status = delegation.delegation_status(self.scope, 'codex', 'parent')
        self.assertIsNone(status['budgets'])
        self.assertEqual(status['capacity']['unresolved_managed_workers'], 0)
        row = self.record(result['attempt_id'])
        self.assertEqual(row['data']['role'], 'check')
        self.assertEqual(row['data']['resource_units'], 0)
        self.assertIsNone(row['body']['d6_plan'])
        self.assertIsNone(row['data']['binding'])
        execution = evidence._read(self.scope, attempt['execution'])
        self.assertEqual(evidence._read(self.scope, execution['output'])['text'], 'fixture failed')
        plan = evidence._load_plan(self.scope, self.identity)
        proof = dict(version=2, kind='check-writer-reconciliation', identity=self.identity.copy(),
            expected_pins=task['pins'], expected_revision=task['revision'], scope_id=state._scope_id(self.scope),
            attempt_id=attempt['id'], dispatch_id=row['owner']['dispatch_id'], assignment_digest=row['data']['assignment']['digest'],
            claims_digest=evidence._digest(row['data']['claims']), candidate_digest=attempt['candidate_digest'],
            current_candidate_digest=evidence._digest(evidence.capture_candidate(self.scope, plan)),
            plan_digest=attempt['plan_digest'], execution_digest=attempt['execution']['digest'], stopped=True,
            descendants_stopped=True, observation_ref='fixture:synchronous-child-reaped-no-descendants',
            coordinator_ref='fixture:independent-exit-and-output-inspection')
        settled = evidence.reconcile_check(self.scope, self.request(attempt_id=attempt['id'], host_proof=self.private_proof(proof)))
        self.assertEqual(settled['attempt']['status'], 'failed')
        self.assertEqual(settled['attempt']['exit_code'], 7)
        self.assertEqual(settled['attempt']['execution'], attempt['execution'])
        self.assertEqual(self.record(attempt['id'])['data']['status'], 'settled')
        self.assertEqual(len(self.task()['evidence']['attempts']), 1)
        self.assertNotIn('delegation', self.task())

    def test_declared_mutable_check_resource_blocks_another_check_before_execution(self):
        # Catches a check admission that ignores another unresolved check's mutable resource.
        code = "from pathlib import Path; p=Path(%r); p.write_text(p.read_text() + 'run\\n')" % str(self.counter)
        plan = self.check_plan(code, mutable=True)
        other = self.identity | dict(session_id='other-parent', task_id='other-task')
        state.start_task(self.scope, other | dict(mode='editable', decision_ref='user:accepted'))
        evidence.plan_evidence(self.scope, other | dict(expected_revision=1, plan=plan))
        first = evidence.run_check(self.scope, self.request(check_id='unit'))
        before = self.records()
        with self.assertRaises(state.StateError):
            evidence.run_check(self.scope, other | dict(expected_revision=2, check_id='unit'))
        self.assertEqual(self.counter.read_text(), 'run\n')
        self.assertEqual(self.records(), before)
        self.assertNotEqual(self.record(first['attempt_id'])['data']['status'], 'settled')
        other_task = state.task_status(self.scope, 'codex', 'other-parent')['task']
        self.assertEqual(other_task['evidence']['attempts'], [])

    def test_task_only_coordinator_replay_and_exact_proof_settle_without_candidate(self):
        # Catches fake plan/native requirements and reservation replay consuming ownership twice.
        request = self.operation_request()
        first = self.reserve_coordinator(request)
        original = self.task()
        replay = self.reserve_coordinator(request)
        self.assertEqual(replay['attempt_id'], first['attempt_id'])
        self.assertFalse(replay['reservation_admitted'])
        self.assertNotIn('launch_admitted', first)
        self.assertEqual(self.task(), original)
        row = self.record(first['attempt_id'])
        for key in ('candidate', 'binding', 'plan_digest', 'host_capacity'):
            self.assertIsNone(row['data'][key])
        self.assertEqual(row['data']['resource_units'], 0)
        self.assertNotIn('evidence', original)
        self.assertNotIn('delegation', original)
        proof = self.coordinator_proof(first['attempt_id'])
        settled = self.reconcile_operation(proof)
        self.assertEqual(settled['outcome'], 'completed')
        self.assertEqual(self.record(first['attempt_id'])['data']['status'], 'settled')
        self.assertNotIn('evidence', self.task())
        self.assertNotIn('delegation', self.task())
        self.assertTrue(self.reserve_coordinator(self.operation_request(dispatch_id='operation-2',
            operation=dict(id='operation-2', kind='coordinator-work')))['reservation_admitted'])

    def test_unresolved_exact_check_cannot_execute_again_under_a_new_dispatch(self):
        self.check_plan('pass')
        original = self.request(check_id='unit', dispatch_id='first-check')
        with patch.object(evidence, '_execute', wraps=evidence._execute) as executed:
            first = evidence.run_check(self.scope, original)
            self.assertEqual(evidence.run_check(self.scope, original)['attempt_id'], first['attempt_id'])
            with self.assertRaises(state.StateError):
                evidence.run_check(self.scope, self.request(check_id='unit', dispatch_id='second-check'))
            with self.assertRaises(state.StateError):
                evidence.run_check(self.scope, original | dict(check_id='another-check'))
        self.assertEqual(executed.call_count, 1)
        self.assertEqual(len(self.task()['evidence']['attempts']), 1)

    def test_matching_coordinator_job_retains_original_attempt_limit_without_native_consumption(self):
        self.check_plan()
        request = self.operation_request(operation=dict(id='scoped-operation', kind='coordinator-work'))
        plan = self.fixture.coordinator_resource_plan([dict(id='scoped-operation', role='coordinator', claims=request['claims'],
            dependencies=[], max_attempts=1)])
        delegation.plan_delegation(self.scope, self.request(plan=plan))
        request = self.operation_request(operation=dict(id='scoped-operation', kind='coordinator-work'))
        first = self.reserve_coordinator(request)
        self.assertEqual(self.record(first['attempt_id'])['data']['job_id'], 'scoped-operation')
        proof = self.coordinator_proof(first['attempt_id'])
        self.reconcile_operation(proof)
        with self.assertRaises(state.StateError):
            self.reserve_coordinator(self.operation_request(dispatch_id='new-dispatch', operation=request['operation']))
        self.assertEqual(self.task()['delegation']['attempts_consumed'], 0)
        self.assertEqual(self.task()['delegation']['resource_units_consumed'], 0)

    def test_uncertain_verifier_barrier_recovers_without_inventing_ready_or_native_facts(self):
        self.fixture.ready_proof_fixture()
        with patch.object(evidence, 'capture_candidate', side_effect=RuntimeError('fixture verifier crash')):
            with self.assertRaises(RuntimeError):
                self.fixture.verify()
        barrier = next(r for r in self.records() if r['data']['role'] == 'coordinator')
        proof = self.coordinator_proof(barrier['id'], write_output=False)
        with self.assertRaises(state.StateError):
            self.reconcile_operation(proof | dict(operation_descendants_stopped=False))
        self.assertNotEqual(self.record(barrier['id'])['data']['status'], 'settled')
        recovered = self.reconcile_operation(proof)
        self.assertEqual(recovered['outcome'], 'completed')
        self.assertNotEqual(self.task()['state'], 'DONE')
        self.assertNotIn('verification', self.task()['evidence'])
        self.assertNotIn('delegation', self.task())
        self.assertTrue(self.reconcile_operation(proof)['replayed'])
        self.assertTrue(self.fixture.verify()['verified'])

    def test_coordinator_settlement_replay_cannot_change_payload_or_release_again(self):
        first = self.reserve_coordinator(self.operation_request())
        proof = self.coordinator_proof(first['attempt_id'])
        self.reconcile_operation(proof)
        original = self.task()
        self.assertTrue(self.reconcile_operation(proof)['replayed'])
        self.assertEqual(self.task(), original)
        request = self.request(attempt_id=first['attempt_id'], host_proof=self.private_proof(proof), assessment=dict(
            outcome='failed', changed_paths=[], result_assessment='Changed outcome', partial_change_assessment='No extra effects',
            evidence=['fixture:changed'], coordinator_ref='fixture:changed'))
        with self.assertRaises(state.StateError):
            delegation.reconcile_assignment(self.scope, request)
        self.assertEqual(self.task(), original)

    def test_unknown_readonly_check_footprint_reserves_conservative_checkout_scope(self):
        self.check_plan()
        plan = evidence._load_plan(self.scope, self.identity)
        plan['checks'][0]['dependencies'] = []
        evidence.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:unknown-footprint'))
        result = evidence.run_check(self.scope, self.request(check_id='unit'))
        claims = self.record(result['attempt_id'])['data']['claims']
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['path'], str(self.repo))
        self.assertEqual(claims[0]['mode'], 'write')
        self.assertTrue(claims[0]['directory'])

    def test_readonly_task_can_run_readonly_command_with_conservative_exclusive_ownership(self):
        self.check_plan()
        plan = evidence._load_plan(self.scope, self.identity)
        plan['checks'][0]['dependencies'] = []
        readonly = self.identity | dict(session_id='readonly', task_id='readonly-task')
        state.start_task(self.scope, readonly | dict(mode='read-only', decision_ref='user:accepted'))
        evidence.plan_evidence(self.scope, readonly | dict(expected_revision=1, plan=plan))
        result = evidence.run_check(self.scope, readonly | dict(expected_revision=2, check_id='unit'))
        self.assertEqual(result['attempt']['exit_code'], 0)
        self.assertEqual(result['task']['mode'], 'read-only')
        record = self.record(result['attempt_id'])
        self.assertEqual(record['data']['claims'][0]['mode'], 'write')
        self.assertEqual(record['data']['claims'][0]['path'], str(self.repo))
        self.assertIsNone(record['data']['binding'])

    def test_actual_check_diagnostics_survive_unavailable_post_execution_candidate(self):
        self.check_plan("print('actual exit seven'); raise SystemExit(7)")
        original = evidence.capture_candidate
        calls = []
        def capture(*args):
            calls.append(True)
            if len(calls) > 1:
                raise state.StateError('fixture candidate unavailable')
            return original(*args)
        request = self.request(check_id='unit', dispatch_id='diagnostic-check')
        with patch.object(evidence, 'capture_candidate', side_effect=capture):
            result = evidence.run_check(self.scope, request)
        self.assertEqual(result['attempt']['exit_code'], 7)
        execution = evidence._read(self.scope, result['attempt']['execution'])
        self.assertEqual(evidence._read(self.scope, execution['output'])['text'], 'actual exit seven')
        self.assertEqual(self.record(result['attempt_id'])['data']['status'], 'uncertain')
        with patch.object(evidence, '_execute', side_effect=AssertionError('replay executed again')):
            self.assertEqual(evidence.run_check(self.scope, request)['attempt']['execution'], result['attempt']['execution'])

    def test_wrong_operation_or_resource_proof_retains_coordinator_ownership(self):
        # Catches release on a true stop flag attached to a different operation/resource.
        first = self.reserve_coordinator(self.operation_request())
        proof = self.coordinator_proof(first['attempt_id'])
        wrong_resources = evidence._read(self.scope, proof['resource_observations']) | dict(claims_digest='f' * 64)
        changes_to_proof = [dict(operation_id='another-operation'), dict(claims_digest='f' * 64),
            dict(resource_observations=fixture_artifact(self.scope,self.identity, wrong_resources))]
        original, original_row = self.task(), self.record(first['attempt_id'])
        for delta in changes_to_proof:
            with self.subTest(delta=delta):
                with self.assertRaises(state.StateError):
                    self.reconcile_operation(proof | delta)
                self.assertEqual(self.task(), original)
                self.assertEqual(self.record(first['attempt_id']), original_row)
                with self.assertRaises(state.StateError):
                    self.reserve_coordinator(self.operation_request(dispatch_id='conflicting-operation',
                        operation=dict(id='conflicting-operation', kind='coordinator-work')))
        self.assertEqual(self.reconcile_operation(proof)['outcome'], 'completed')



if __name__ == '__main__':
    unittest.main()


class Stage4NativeReviewTests(unittest.TestCase):
    """Synthetic native facts only; preserve the separate D5 and D6 digest domains."""
    setUp = AdmissionTests.setUp
    request = AdmissionTests.request
    path_claim = AdmissionTests.path_claim
    plan_value = AdmissionTests.plan_value
    plan = AdmissionTests.plan
    prepare_request = AdmissionTests.prepare_request
    raw_assignment = AdmissionTests.raw_assignment
    worker_reconciliation_request = AdmissionTests.worker_reconciliation_request

    def review_plan(self, **updates):
        return self.plan_value(jobs=[dict(id='review', role='review', claims=[self.path_claim(mode='read')],
            dependencies=[], max_attempts=3)], **updates)

    def review_request(self, **updates):
        request = self.prepare_request(job_id='review', dispatch_id='review-dispatch')
        request.pop('brief')
        return request | dict(coverage=['general', 'security'], criteria=['works'],
            assignment='Review app.py and callers read-only; return the exact D5 report.') | updates

    def prepared(self, **updates):
        return evidence.prepare_review(self.scope, self.review_request(**updates))

    def test_review_visibility_preserves_readonly_authority_and_digest_domains(self):
        self.plan(self.review_plan())
        visibility = dict(version=1, role='Reviewer', specialty='Parser safety', custom_role_name='Safety reviewer')
        request = self.review_request(visibility=visibility)
        try:
            prepared = evidence.prepare_review(self.scope, request)
        except state.StateError as error:
            self.fail('Independent review visibility was rejected: ' + str(error))
        binding = evidence.prepared_native_review(self.scope, prepared['attempt_id'])
        self.assertEqual(prepared['visibility'], visibility)
        self.assertEqual(binding['visibility'], visibility)
        body = json.loads(prepared['prompt'])['ownership']
        self.assertEqual(body['role'], 'review')
        self.assertTrue(all(claim['mode'] == 'read' for claim in body['claims']))
        self.assertEqual(body['request']['visibility'], visibility)
        self.assertEqual(binding['task_name'], 'review_' + prepared['attempt_id'])
        self.assertEqual(prepared['assignment_digest'], hashlib.sha256(request['assignment'].encode()).hexdigest())
        self.assertEqual(binding['ledger_assignment_digest'], prepared['ledger_assignment_digest'])
        self.assertNotEqual(binding['ledger_assignment_digest'], prepared['assignment_digest'])
        replay = evidence.prepare_review(self.scope, request)
        self.assertFalse(replay['launch_admitted'])
        self.assertEqual(replay['visibility'], visibility)
        with self.assertRaisesRegex(state.StateError, 'changed payload'):
            evidence.prepare_review(self.scope, request | dict(visibility=visibility | dict(custom_role_name='Other reviewer')))

    def observed(self, prepared, *, turn_status='completed', raw=None, **updates):
        binding = self.api.prepared_native_review_assignment(self.scope, prepared['attempt_id'])
        with patch.object(self.api, 'prepared_native_assignment', return_value=binding):
            observed = AdmissionTests.lifecycle_observation(self, prepared, turn_status=turn_status)
        report = dict(version=1, attempt_id=prepared['attempt_id'], assignment_digest=prepared['assignment_digest'],
            candidate_digest=prepared['candidate_digest'], coverage=['general', 'security'], findings=[], limitations=[])
        observed['result'] = (json.dumps(report) if raw is None else raw) if turn_status == 'completed' else None
        observed['receipt'].update(updates)
        observed['receipt']['result_digest'] = hashlib.sha256(observed['result'].encode()).hexdigest() if observed['result'] else None
        return observed

    def observe(self, prepared, observed=None, request=None):
        request = request or self.request(attempt_id=prepared['attempt_id'], parent_turn_id='parent-turn', spawn_item_id='spawn-item')
        with patch('runtime.adapters.codex.observe_native_review_assignment', return_value=observed or self.observed(prepared)):
            return self.api.observe_assignment(self.scope, request)

    def collection(self, prepared, observed):
        r = observed['receipt']
        report = json.loads(observed['result'])
        return dict(receipt=dict(version=1, mechanism='codex-native-agent-path', native_version=r['native_version'],
            parent_session_id=r['parent_session_id'], parent_turn_id=r['parent_turn_id'], spawn_item_id=r['spawn_item_id'],
            reviewer_id=r['worker_id'], review_turn_id=r['worker_turn_id'], report_item_id=r['result_item_id'],
            assignment_digest=prepared['assignment_digest'], candidate_digest=prepared['candidate_digest'],
            report_digest=r['result_digest'], requested_model=r['spawn_observed_model'], requested_effort=r['spawn_observed_effort'],
            task_name=r['task_name'], agent_path=r['agent_path']), report=report)

    def collect(self, prepared, observation):
        with patch('runtime.adapters.codex.collect_native_review', return_value=observation):
            return evidence.collect_review(self.scope, self.request(attempt_id=prepared['attempt_id'],
                parent_turn_id='parent-turn', spawn_item_id='spawn-item'))

    def test_review_requires_explicit_native_job_and_atomic_pair(self):
        with self.assertRaises(state.StateError):
            evidence.prepare_review(self.scope, self.request(coverage=['general'], criteria=['works'], assignment='Unmanaged review'))
        self.plan(self.review_plan())
        for delta in (dict(job_id='missing'), dict(selection=self.review_request()['selection'] | dict(model='absent'))):
            with self.subTest(delta=delta), self.assertRaises(state.StateError):
                self.prepared(**delta)
        with patch.object(state, '_save', side_effect=state.StateError('fixture atomic failure')):
            with self.assertRaises(state.StateError):
                self.prepared()
        with state._database(self.scope) as db:
            self.assertEqual(self.api._ledger(self.scope, db), [])
            self.assertEqual(state._task(db, state._scope_id(self.scope), 'codex', 'parent')['evidence']['attempts'], [])
        request = self.review_request()
        def reserve(_):
            try:
                return evidence.prepare_review(self.scope, request)
            except state.StateError as error:
                self.assertIn('busy', str(error))
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            admitted = list(pool.map(reserve, range(2)))
        self.assertEqual(sum(item['launch_admitted'] for item in admitted if item), 1)
        admitted = [item or evidence.prepare_review(self.scope, request) for item in admitted]
        self.assertEqual(len({item['attempt_id'] for item in admitted}), 1)
        self.assertEqual(sum(item['launch_admitted'] for item in admitted), 1)

    def test_review_pair_digests_and_replay_precede_discovery_and_candidate(self):
        self.plan(self.review_plan())
        request = self.review_request()
        prepared = evidence.prepare_review(self.scope, request)
        self.assertTrue(prepared['launch_admitted'])
        self.assertFalse(prepared['replayed'])
        self.assertEqual(prepared['revision'], request['expected_revision'] + 1)
        binding = self.api.prepared_native_review_assignment(self.scope, prepared['attempt_id'])
        self.assertEqual(set(binding), {'attempt_id','parent_session_id','task_name','assignment_digest','candidate_digest',
            'requested_model','requested_effort','client_surface','native_version','pins','accepted_identity','scope_id'})
        self.assertEqual(binding['task_name'], 'review_' + prepared['attempt_id'])
        self.assertEqual(prepared['assignment_digest'], hashlib.sha256(request['assignment'].encode()).hexdigest())
        self.assertEqual(binding['assignment_digest'], prepared['ledger_assignment_digest'])
        self.assertNotEqual(prepared['assignment_digest'], binding['assignment_digest'])
        self.assertIn(request['assignment'], prepared['prompt'])
        legacy = evidence.prepared_native_review(self.scope, prepared['attempt_id'])
        self.assertEqual(legacy['assignment_digest'], prepared['assignment_digest'])
        with patch.object(evidence, 'capture_candidate', side_effect=AssertionError('replay captured')), \
             patch('runtime.adapters.codex.discover_native_workers', side_effect=AssertionError('replay discovered')), \
             patch.object(self.api, '_now', return_value=10**12):
            replay = evidence.prepare_review(self.scope, request)
        self.assertFalse(replay['launch_admitted'])
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['attempt_id'], prepared['attempt_id'])
        with self.assertRaises(state.StateError):
            evidence.prepare_review(self.scope, request | dict(assignment='Changed request'))
        status = self.api.delegation_status(self.scope, 'codex', 'parent')
        self.assertEqual(status['budgets']['attempts_consumed'], 1)
        self.assertEqual(status['budgets']['resource_units_consumed'], 1)
        with state._database(self.scope) as db:
            attempts = state._task(db, state._scope_id(self.scope), 'codex', 'parent')['evidence']['attempts']
        self.assertEqual([a['id'] for a in attempts], [prepared['attempt_id']])

    def test_v1_review_collection_matches_linked_lifecycle_and_plaintext_assignment(self):
        self.plan(self.review_plan())
        prepared = self.prepared()
        binding = evidence.prepared_native_review(self.scope, prepared['attempt_id'])
        self.assertEqual(binding['ledger_assignment_digest'], prepared['ledger_assignment_digest'])
        self.assertEqual(binding['assignment_digest'], prepared['assignment_digest'])
        self.assertNotEqual(binding['ledger_assignment_digest'], binding['assignment_digest'])
        observed = self.observed(prepared, mechanism='codex-native-collab-call', agent_path=None)
        self.assertEqual(observed['receipt']['assignment_digest'], binding['ledger_assignment_digest'])
        self.assertEqual(self.observe(prepared, observed)['status'], 'observed')
        collection = self.collection(prepared, observed)
        collection['receipt']['mechanism'] = 'codex-native-collab-call'
        self.assertEqual(collection['receipt']['assignment_digest'], binding['assignment_digest'])
        for key, value in (('mechanism', 'codex-native-agent-path'), ('task_name', 'review_other'),
                           ('reviewer_id', 'other-child'), ('parent_turn_id', 'other-parent-turn'),
                           ('review_turn_id', 'other-turn'), ('report_item_id', 'other-report'),
                           ('report_digest', 'f' * 64), ('candidate_digest', 'f' * 64),
                           ('assignment_digest', binding['ledger_assignment_digest'])):
            bad = deepcopy(collection); bad['receipt'][key] = value
            with self.subTest(key=key), self.assertRaises(state.StateError):
                self.collect(prepared, bad)
        bad = deepcopy(collection); bad['report']['assignment_digest'] = binding['ledger_assignment_digest']
        with self.assertRaises(state.StateError):
            self.collect(prepared, bad)
        with state._database(self.scope) as db:
            record = next(row for row in self.api._ledger(self.scope, db) if row['id'] == prepared['attempt_id'])
        path_history = deepcopy(self.api._worker_history(self.scope, record['data']['observation']))
        native_path = '/root/' + observed['receipt']['task_name']
        path_history[0][1]['receipt']['agent_path'] = native_path
        matching = deepcopy(collection); matching['receipt']['agent_path'] = native_path
        # Isolate route correspondence with identical valid paths; retain real null-path history below.
        with patch.object(self.api, '_worker_history', return_value=path_history):
            self.assertTrue(evidence._review_quality_matches(self.scope, prepared['attempt'], record, matching))
            mismatched = deepcopy(matching); mismatched['receipt']['mechanism'] = 'codex-native-agent-path'
            with self.subTest(case='mechanism only with identical valid paths'), self.assertRaises(state.StateError):
                self.collect(prepared, mismatched)
        self.assertEqual(self.collect(prepared, collection)['attempt']['status'], 'collected')
        self.assertNotEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'settled')
        assessed = evidence.assess_review(self.scope, self.request(attempt_id=prepared['attempt_id'], assessment=dict(
            coverage=['general', 'security'], criteria=['works'], qualification='Independent fixture expertise',
            independence='Separate observed fixture reviewer', semantic_coverage='Read requirements and code',
            dispositions=[], decision_refs=['user:accepted'])))
        self.assertEqual(assessed['attempt']['status'], 'assessed')
        self.assertNotEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'settled')

    def test_review_collection_correlates_lifecycle_and_assessment_never_settles(self):
        self.plan(self.review_plan())
        prepared = self.prepared()
        observed = self.observed(prepared)
        first = self.observe(prepared, observed)
        self.assertEqual(first['status'], 'observed')
        repeat = self.observe(prepared, observed)
        self.assertTrue(repeat['replayed'])
        self.assertEqual(repeat['revision'], first['revision'])
        collection = self.collection(prepared, observed)
        for key in ('reviewer_id', 'review_turn_id', 'report_item_id', 'report_digest', 'spawn_item_id'):
            bad = deepcopy(collection); bad['receipt'][key] = 'wrong'
            with self.subTest(key=key), self.assertRaises(state.StateError):
                self.collect(prepared, bad)
        result = self.collect(prepared, collection)
        self.assertEqual(result['attempt']['status'], 'collected')
        assessed = evidence.assess_review(self.scope, self.request(attempt_id=prepared['attempt_id'], assessment=dict(
            coverage=['general', 'security'], criteria=['works'], qualification='Independent fixture expertise',
            independence='Separate observed reviewer', semantic_coverage='Read requirements and code', dispositions=[],
            decision_refs=['user:accepted'])))
        self.assertEqual(assessed['attempt']['status'], 'assessed')
        self.assertNotEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'settled')
        self.assertFalse(evidence.verify_completion(self.scope, self.request(purpose='completion'))['verified'])
        request = self.worker_reconciliation_request(prepared, changed_paths=[])
        self.assertEqual(self.api.reconcile_assignment(self.scope, request)['outcome'], 'completed')
        self.assertTrue(self.api.reconcile_assignment(self.scope, request)['replayed'])
        with patch('runtime.adapters.codex.discover_native_workers', side_effect=AssertionError('settled replay discovered')):
            replay = evidence.prepare_review(self.scope, self.review_request() | dict(expected_revision=1))
        self.assertFalse(replay['launch_admitted'])
        self.assertEqual(replay['attempt']['status'], 'assessed')

    def test_review_malformed_incomplete_and_mutated_results_remain_recoverable(self):
        self.plan(self.review_plan())
        previous = None
        for number, kind in enumerate(('malformed', 'incomplete', 'mutated')):
            with self.subTest(kind=kind):
                extra = dict(dispatch_id='review-' + str(number))
                if previous:
                    extra.update(retry_of=previous['attempt_id'], diagnostic=dict(prior_attempt_id=previous['attempt_id'],
                        cause='Fixture prior failed review', change='New bounded review', evidence=['fixture:new-' + kind]))
                prepared = self.prepared(**extra)
                observed = self.observed(prepared, raw='malformed') if kind == 'malformed' else self.observed(prepared,
                    turn_status='inProgress' if kind == 'incomplete' else 'completed')
                self.observe(prepared, observed)
                if kind == 'mutated':
                    (self.repo / 'app.py').write_text('value = 2\n')
                    with self.assertRaises(state.StateError):
                        self.collect(prepared, self.collection(prepared, observed))
                elif kind == 'malformed':
                    self.assertEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'observed')
                else:
                    self.assertIsNone(observed['result'])
                request = self.worker_reconciliation_request(prepared, outcome='failed',
                    changed_paths=['app.py'] if kind == 'mutated' else [])
                bad = self.worker_reconciliation_request(prepared, outcome='failed', proof_delta=dict(descendants_stopped=False))
                with self.assertRaises(state.StateError):
                    self.api.reconcile_assignment(self.scope, bad)
                self.assertEqual(self.api.reconcile_assignment(self.scope, request)['outcome'], 'failed')
                previous = prepared
        with self.assertRaises(state.StateError):
            self.prepared(dispatch_id='fourth', retry_of=previous['attempt_id'], diagnostic=dict(
                prior_attempt_id=previous['attempt_id'], cause='Original exhausted', change='Cannot reset', evidence=['fixture:new-fourth']))
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 3)

    def test_review_contradictory_marker_and_cancelled_never_started_claims(self):
        self.plan(self.review_plan())
        prepared = self.prepared()
        wrong = self.observed(prepared, task_name='worker_' + prepared['attempt_id'])
        self.assertEqual(self.observe(prepared, wrong)['status'], 'uncertain')
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(prepared))
        good = self.observed(prepared)
        self.observe(prepared, good)
        conflict = self.observed(prepared, worker_id='different-child')
        self.assertEqual(self.observe(prepared, conflict)['status'], 'uncertain')
        request = self.worker_reconciliation_request(prepared, outcome='failed', changed_paths=[])
        self.api.reconcile_assignment(self.scope, request)
        next_review = self.prepared(dispatch_id='never-started', retry_of=prepared['attempt_id'], diagnostic=dict(
            prior_attempt_id=prepared['attempt_id'], cause='Contradictory review', change='New attempt', evidence=['fixture:diagnosed']))
        state.update_task(self.scope, self.request(state='CANCELLED', reason='User cancelled launch', reference='user:cancel'))
        task = state.task_status(self.scope, 'codex', 'parent')['task']
        with self.assertRaises(state.StateError):
            state.start_task(self.scope, self.identity | dict(task_id='new', mode='editable', decision_ref='user:accepted',
                replaces=dict(task_id='task', expected_revision=task['revision'])))
        request = self.worker_reconciliation_request(next_review, outcome='cancelled', changed_paths=[])
        self.assertEqual(self.api.reconcile_assignment(self.scope, request)['outcome'], 'cancelled')
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 2)

    def test_review_job_is_readonly_and_worker_entry_cannot_admit_it(self):
        bad = self.review_plan(); bad['jobs'][0]['claims'][0]['mode'] = 'write'
        with self.assertRaises(state.StateError):
            self.plan(bad)
        self.plan(self.review_plan())
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, self.prepare_request(job_id='review'))
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['assignments'], [])

    def test_review_dependencies_require_settled_checks_without_native_consumption(self):
        plan = self.review_plan()
        plan['jobs'].append(dict(id='unit', role='check', claims=[self.path_claim(mode='read')], dependencies=[], max_attempts=1))
        plan['jobs'][0]['dependencies'] = ['unit']
        self.plan(plan)
        with self.assertRaises(state.StateError):
            self.prepared()
        checked = self.fixture.check()
        with self.assertRaises(state.StateError):
            self.prepared()
        self.fixture.reconcile(checked)
        prepared = self.prepared()
        self.assertTrue(prepared['launch_admitted'])
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 1)

    def test_planned_check_requires_its_worker_before_reservation(self):
        plan = self.plan_value()
        plan['jobs'].append(dict(id='unit', role='check', claims=[self.path_claim(mode='read')],
            dependencies=['edit'], max_attempts=1))
        self.plan(plan)
        with patch.object(evidence, '_execute', side_effect=AssertionError('check ran without its planned worker')):
            with self.assertRaisesRegex(state.StateError, 'dependency prerequisite'):
                self.fixture.check()
        task = state.task_status(self.scope, 'codex', 'parent')['task']
        self.assertEqual(task['evidence']['attempts'], [])
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['assignments'], [])

    def test_review_preserves_plain_assignment_limit_without_relaxing_external_request_cap(self):
        self.plan(self.review_plan())
        assignment = 'r' * 65536
        prepared = self.prepared(assignment=assignment)
        self.assertEqual(prepared['assignment_digest'], hashlib.sha256(assignment.encode()).hexdigest())
        prompt = json.loads(prepared['prompt'])
        self.assertEqual(prompt['assignment'], assignment)
        self.assertEqual(prompt['report_contract']['assignment_digest'], prepared['assignment_digest'])
        self.assertEqual(prompt['report_contract']['attempt_id'], prepared['attempt_id'])
        with self.assertRaises(state.StateError):
            self.prepared(assignment='r' * 65537)
        with self.assertRaises(state.StateError):
            self.prepared(assignment='😀' * 65536)

    def test_review_original_round_limit_survives_changed_evidence_plan(self):
        plan = evidence._load_plan(self.scope, self.identity)
        plan['review_limit'] = 1
        evidence.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:one-original-review'))
        self.plan(self.review_plan())
        first = self.prepared()
        self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(first, outcome='cancelled', changed_paths=[]))
        plan['review_limit'] = 3
        evidence.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:cannot-reset-history'))
        with self.assertRaisesRegex(state.StateError, 'Original review round limit'):
            self.prepared(dispatch_id='reset-attempt', retry_of=first['attempt_id'], diagnostic=dict(
                prior_attempt_id=first['attempt_id'], cause='First round cancelled', change='Try changed evidence plan',
                evidence=['fixture:new-diagnosis']))
        status = self.api.delegation_status(self.scope, 'codex', 'parent')
        self.assertEqual(status['budgets']['attempts_consumed'], 1)
        self.assertEqual(len(status['assignments']), 1)

    def assess_current_review(self, prepared, **updates):
        return evidence.assess_review(self.scope, self.request(attempt_id=prepared['attempt_id'], assessment=dict(
            coverage=['general', 'security'], criteria=['works'], qualification='Independent fixture expertise',
            independence='Separate observed reviewer', semantic_coverage='Read requirements and code', dispositions=[],
            decision_refs=['user:accepted']) | updates))

    def quality_review_fixture(self, findings=None):
        self.fixture.checked()
        self.plan(self.review_plan())
        prepared = self.prepared()
        first = self.observed(prepared)
        if findings:
            report = json.loads(first['result']); report['findings'] = findings
            first['result'] = json.dumps(report)
            first['receipt']['result_digest'] = hashlib.sha256(first['result'].encode()).hexdigest()
        self.observe(prepared, first)
        self.collect(prepared, self.collection(prepared, first))
        assessed = self.assess_current_review(prepared)
        return prepared, assessed['attempt']

    def test_later_malformed_review_cannot_settle_completed_using_previous_quality(self):
        prepared, original = self.quality_review_fixture()
        later = self.observed(prepared, raw='malformed later result', worker_turn_id='later-turn', result_item_id='later-final')
        self.observe(prepared, later)
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(prepared, changed_paths=[]))
        with self.assertRaises(state.StateError):
            self.assess_current_review(prepared)
        recovery = self.api.reconcile_assignment(self.scope,
            self.worker_reconciliation_request(prepared, outcome='failed', changed_paths=[]))
        self.assertEqual(recovery['outcome'], 'failed')
        verified = evidence.verify_completion(self.scope, self.request(purpose='completion'))
        self.assertFalse(verified['verified'])
        current = next(a for a in verified['task']['evidence']['attempts'] if a['id'] == prepared['attempt_id'])
        self.assertEqual(current['collected'], original['collected'])
        self.assertEqual(current['assessment'], original['assessment'])

    def test_later_interrupted_review_release_does_not_reuse_previous_quality(self):
        prepared, original = self.quality_review_fixture()
        self.observe(prepared, self.observed(prepared, turn_status='interrupted', worker_turn_id='later-turn'))
        recovery = self.api.reconcile_assignment(self.scope,
            self.worker_reconciliation_request(prepared, outcome='failed', changed_paths=[]))
        self.assertEqual(recovery['outcome'], 'failed')
        verified = evidence.verify_completion(self.scope, self.request(purpose='completion'))
        self.assertFalse(verified['verified'])
        self.assertTrue(any('qualified review is missing or stale' in b for b in verified['blockers']))
        current = next(a for a in verified['task']['evidence']['attempts'] if a['id'] == prepared['attempt_id'])
        self.assertEqual(current['collected'], original['collected'])
        self.assertEqual(current['assessment'], original['assessment'])
        with self.assertRaises(state.StateError):
            self.assess_current_review(prepared)

    def test_matching_later_uncollected_review_can_assess_before_delayed_stop(self):
        self.fixture.checked()
        self.plan(self.review_plan())
        prepared = self.prepared()
        self.observe(prepared)
        later = self.observed(prepared, worker_turn_id='later-turn', result_item_id='later-final')
        self.observe(prepared, later)
        self.collect(prepared, self.collection(prepared, later))
        self.assess_current_review(prepared)
        self.assertFalse(evidence.verify_completion(self.scope, self.request(purpose='completion'))['verified'])
        self.assertEqual(self.api.reconcile_assignment(self.scope,
            self.worker_reconciliation_request(prepared, changed_paths=[]))['outcome'], 'completed')
        self.assertTrue(evidence.verify_completion(self.scope, self.request(purpose='completion'))['verified'])
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 1)

    def test_superseded_review_findings_survive_failed_ownership_recovery(self):
        finding = dict(id='security-prior', severity='high', scope='security', status='confirmed',
            location='app.py:1', scenario='Reachable fixture unsafe path', evidence='Independent code tracing',
            counterevidence='No applicable guard')
        prepared, original = self.quality_review_fixture([finding])
        later = self.observed(prepared, worker_turn_id='later-turn', result_item_id='later-final')
        self.observe(prepared, later)
        with self.assertRaisesRegex(state.StateError, 'not pending'):
            self.collect(prepared, self.collection(prepared, later))
        self.api.reconcile_assignment(self.scope,
            self.worker_reconciliation_request(prepared, outcome='failed', changed_paths=[]))
        verified = evidence.verify_completion(self.scope, self.request(purpose='completion'))
        self.assertFalse(verified['verified'])
        self.assertTrue(any('security finding needs fresh scoped review' in b for b in verified['blockers']))
        self.assertTrue(any('qualified review is missing or stale' in b for b in verified['blockers']))
        self.assertEqual(evidence._read(self.scope, original['collected'])['report']['findings'], [finding])
        fresh = self.prepared(dispatch_id='fresh-quality', retry_of=prepared['attempt_id'], diagnostic=dict(
            prior_attempt_id=prepared['attempt_id'], cause='Prior review superseded', change='Explicit fresh review',
            evidence=['fixture:fresh-quality-diagnosis']))
        observed = self.observed(fresh)
        self.observe(fresh, observed)
        self.collect(fresh, self.collection(fresh, observed))
        self.assess_current_review(fresh, rechecked_findings=[dict(attempt_id=prepared['attempt_id'],
            finding_id=finding['id'], status='resolved', reference='fixture:fresh-independent-inspection')])
        self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(fresh, changed_paths=[]))
        self.assertTrue(evidence.verify_completion(self.scope, self.request(purpose='completion'))['verified'])

    def test_lost_committed_review_response_keeps_pair_budget_and_current_task(self):
        self.plan(self.review_plan())
        request = self.review_request()
        committed = []
        prepare_native = delegation._prepare_native
        def lose_response(scope, original_request, *, role):
            response = prepare_native(scope, original_request, role=role)
            self.assertTrue(response['launch_admitted'])
            with state._database(scope) as visible:
                records = self.api._ledger(scope, visible)
                record = next(r for r in records if r['id'] == response['attempt']['id'])
                self.assertEqual([r['id'] for r in records], [record['id']])
                task = state._task(visible, state._scope_id(scope), 'codex', 'parent')
            self.assertEqual(task['revision'], request['expected_revision'] + 1)
            self.assertEqual(task['delegation']['attempts_consumed'], 1)
            self.assertEqual(task['delegation']['resource_units_consumed'], 1)
            attempts = task['evidence']['attempts']
            self.assertEqual([a['id'] for a in attempts], [record['id']])
            self.assertEqual(attempts[0]['ledger_assignment_digest'], record['data']['assignment']['digest'])
            self.assertEqual(attempts[0]['assignment_digest'], hashlib.sha256(request['assignment'].encode()).hexdigest())
            state.update_task(scope, self.identity | dict(expected_revision=task['revision'], state='BLOCKED',
                reason='Coordinator update after durable admission', reference='user:hold'))
            with state._database(scope) as visible:
                committed.append(deepcopy(state._task(visible, state._scope_id(scope), 'codex', 'parent')))
            raise RuntimeError('fixture committed review response lost')
        with patch.object(delegation, '_prepare_native', side_effect=lose_response):
            with self.assertRaisesRegex(RuntimeError, 'fixture committed review response lost'):
                evidence.prepare_review(self.scope, request)
        with patch('runtime.adapters.codex.discover_native_workers', side_effect=AssertionError('replay discovered')), \
             patch.object(evidence, 'capture_candidate', side_effect=AssertionError('replay captured')):
            replay = evidence.prepare_review(self.scope, request)
        self.assertFalse(replay['launch_admitted'])
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['task'], committed[0])
        self.assertEqual(replay['task']['state'], 'BLOCKED')
        self.assertEqual(replay['attempt']['id'], committed[0]['evidence']['attempts'][0]['id'])
        self.assertEqual(replay['task']['delegation']['attempts_consumed'], 1)
        self.assertEqual(replay['task']['delegation']['resource_units_consumed'], 1)


    def test_review_collection_keeps_admitted_version_across_observer_updates(self):
        self.plan(self.review_plan())
        prepared = self.prepared()
        binding = evidence.prepared_native_review(self.scope, prepared['attempt_id'])
        self.assertEqual(binding['native_version'], '0.163.0')
        self.assertEqual(binding.get('client_surface'), 'codex-cli')
        observed = self.observed(prepared, current_observer_version='0.165.0',
            parent_creation_version='0.164.0-alpha.11.1', worker_creation_version='0.164.0-alpha.11.1')
        self.assertEqual(self.observe(prepared, observed)['status'], 'observed')
        collection = self.collection(prepared, observed)
        collection['receipt'].update(current_observer_version='0.166.0', parent_creation_version='0.164.0-alpha.11.1',
            worker_creation_version='0.164.0-alpha.11.1')
        result = self.collect(prepared, collection)
        receipt = evidence._read(self.scope, result['attempt']['collected'])['receipt']
        self.assertEqual(receipt['native_version'], '0.163.0')
        self.assertEqual(receipt['current_observer_version'], '0.166.0')
