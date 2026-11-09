"""Private guidance-cache fixtures; no network, models or live client changes."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import importlib
import json
import hashlib
import os
import sqlite3
import threading
import unittest
from unittest.mock import patch

from runtime import delegation, evidence, state


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


if __name__ == '__main__':
    unittest.main()
