"""Optional upload after completion verification (D9 integration): synthetic session files and local fakes only."""

from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from runtime import (langfuse, observability_credentials as credentials, observability_export as export,
                     observability_hook as hook, observability_settings as settings, startup, state)
from runtime.adapters import codex_usage
from tests import test_runtime
from tests.test_langfuse import protocol_server


PACKAGE = Path(__file__).resolve().parents[1]
SESSION = "session-1"
TASK = "hook-task"
DESTINATION = "https://cloud.langfuse.com"
PROJECT = "lf-project"
VERSION = "0.167.0"
SECRET = "sk-live-PRIVATE user said: my password is hunter2"
USAGE = dict(input_tokens=60, cached_input_tokens=20, cache_write_input_tokens=0,
             output_tokens=40, reasoning_output_tokens=10, total_tokens=100)
ZERO = dict.fromkeys(USAGE, 0)


def ledger(records):
    """Replace only the task's ledger query; state validation keeps the real ledger."""
    from runtime import delegation
    original = delegation._ledger
    return patch("runtime.delegation._ledger", side_effect=lambda scope, db, **kwargs:
                 records if kwargs.get("task") is not None else original(scope, db, **kwargs))


def stamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class FakeConnection:
    """Stands in for `langfuse._Connection`; records each POST and never opens a socket."""
    created, posts, outcome = [], [], "accepted"

    def __init__(self, destination, project_id, seconds, credentials=None):
        FakeConnection.created.append((destination, project_id, credentials))

    def ingest(self, path, body):
        FakeConnection.posts.append((path, body))
        return FakeConnection.outcome


class HookFixture(unittest.TestCase):
    def setUp(self):
        self.fixture = test_runtime.RuntimeTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.canonical()
        self.scope = self.fixture.scope
        self.original_acquire = startup.acquire_project_entry
        self.observation = json.loads(self.fixture.test_acquisition.read_bytes())["observation"]
        acquisition = patch.object(startup, "acquire_project_entry", side_effect=lambda *args, **kwargs:
            self.original_acquire(*args, **(kwargs | dict(qualifier=lambda record, facts: self.observation))))
        acquisition.start()
        self.addCleanup(acquisition.stop)
        state.start_task(self.scope, dict(client="codex", session_id=SESSION, task_id=TASK,
            scope_ref="user:fixture", mode="read-only", decision_ref="user:fixture"))
        self.task = deepcopy(state.task_status(self.scope, "codex", SESSION)["task"])
        self.started = int(time.time()) - 3600
        self.task["lifecycle"] = dict(self.task["lifecycle"], created_at=self.started)
        self.task_ref = {key: self.task[key] for key in ("client", "session_id", "task_id")}
        codex = tempfile.TemporaryDirectory()
        self.addCleanup(codex.cleanup)
        self.codex_home = Path(codex.name).resolve()
        environment = patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)})
        environment.start()
        self.addCleanup(environment.stop)
        FakeConnection.created, FakeConnection.posts, FakeConnection.outcome = [], [], "accepted"
        self.exports = self.scope.runtime.state_dir / "observability" / "exports"

    # Settings

    def enable(self, destination=DESTINATION, *, source=None, repositories=None, collection=True, opt_in=True):
        request = dict(expected_revision=0, destination=destination, langfuse_project_id=PROJECT)
        if source is not None:
            request["credential_source"] = source
        settings.configure(self.scope, request)
        if opt_in:
            with patch("runtime.langfuse.verify_project", return_value=None), \
                    patch("runtime.observability_credentials.load", return_value=("pk-lf-x", "sk-lf-x")):
                settings.enable(self.scope, dict(expected_revision=1))
        self.project(repositories, collection)
        self.key = settings.destination_key(langfuse.validate_destination(destination), PROJECT)

    def project(self, repositories=None, collection=True):
        repositories = [self.scope.repository_id] if repositories is None else repositories
        if self.scope.repository_id in repositories:
            settings.configure_project(self.scope, dict(expected_revision=0, repository_ids=sorted(repositories),
                                                         desired_collection=collection))
            return
        # configure-project refuses a list without this repository; write the folder's record directly.
        project = {}
        with settings._guard(self.scope, write=True, create=True, project=project) as owner:
            owner.load()
            owner.save(dict(version=1, revision=1, binding=owner.binding, repository_ids=sorted(repositories),
                            desired_collection=collection))

    # Synthetic session file for the coordinator thread

    def session(self, version=VERSION, usage=USAGE, thread=SESSION):
        today = datetime.now(timezone.utc).date()
        folder = self.codex_home / "sessions" / f"{today.year:04d}" / f"{today.month:02d}" / f"{today.day:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        rows = [
            {"timestamp": stamp(self.started - 600), "type": "session_meta",
             "payload": {"id": thread, "timestamp": stamp(self.started - 600), "cli_version": version,
                         "instructions": SECRET}},
            {"timestamp": stamp(self.started - 500), "type": "turn_context",
             "payload": {"model": "gpt-5.5", "effort": "high", "user_instructions": SECRET}},
            self.token(self.started - 300, ZERO),
            {"timestamp": stamp(self.started + 10), "type": "response_item",
             "payload": {"type": "message", "content": [{"type": "input_text", "text": SECRET}]}},
            self.token(self.started + 60, usage)]
        path = folder / f"rollout-2026-01-01T00-00-00-{thread}.jsonl"
        path.write_bytes(b"\n".join(json.dumps(row).encode() for row in rows) + b"\n")
        os.chmod(path, 0o600)
        return path

    def token(self, seconds, counts):
        return {"timestamp": stamp(seconds), "type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": counts, "last_token_usage": counts, "model_context_window": 1_000_000}}}

    # Verification and upload state

    def report(self, **changes):
        return dict(ok=True, admission_mode="ordinary", verified=True, task=deepcopy(self.task),
                    implementation_verified=True, delivery_complete=False, waiting_for_merge=False, blockers=[],
                    candidate_digest=None, merge_authority=False, limitations=["fixture"]) | changes

    def request(self, purpose="completion"):
        return dict(client="codex", session_id=SESSION, task_id=TASK, expected_revision=self.task["revision"],
                    purpose=purpose)

    def run_hook(self, report=None, request=None, *, qualified=True):
        versions = frozenset({VERSION}) if qualified else frozenset()
        with patch.object(codex_usage, "QUALIFIED_VERSIONS", versions), \
                patch.object(langfuse, "_Connection", FakeConnection):
            hook.after_completion(self.scope, self.request() if request is None else request,
                                  self.report() if report is None else report)

    def envelope_path(self, key=None):
        name = settings.export_name(state._scope_id(self.scope), self.task_ref, key or self.key)
        return self.exports / (name + ".json")

    def envelope(self):
        return json.loads(self.envelope_path().read_bytes())


class HookTests(HookFixture):
    def test_created_at_unit_is_whole_unix_seconds(self):
        created = state.task_status(self.scope, "codex", SESSION)["task"]["lifecycle"]["created_at"]
        self.assertIs(type(created), int)
        self.assertLessEqual(abs(created - time.time()), 120)

    def test_enabled_upload_is_accepted_once_then_nothing_to_send(self):
        self.session()
        with protocol_server() as server:
            self.enable(server.url)
            with patch.object(codex_usage, "QUALIFIED_VERSIONS", frozenset({VERSION})):
                hook.after_completion(self.scope, self.request(), self.report())
                posts = [item for item in server.requests if item["method"] == "POST"]
                self.assertEqual(len(posts), 1)
                envelope = self.envelope()
                self.assertEqual(envelope["last_result"]["code"], "accepted")
                self.assertEqual([record["outcome"] for record in envelope["export_state"]["records"]], ["accepted"])
                self.assertEqual(envelope["export_state"]["exported_totals"], USAGE)
                self.assertEqual(envelope["revision"], 2)
                hook.after_completion(self.scope, self.request(), self.report())
            self.assertEqual(len([item for item in server.requests if item["method"] == "POST"]), 1)
        again = self.envelope()
        self.assertEqual(again["last_result"]["code"], "nothing_to_send")
        self.assertEqual(again["export_state"], envelope["export_state"])
        raw = self.envelope_path().read_text() + json.dumps(posts[0]["body"])
        for private in (SECRET, SESSION, TASK, str(self.codex_home)):
            self.assertNotIn(private, json.dumps(posts[0]["body"]))
        self.assertNotIn(SECRET, raw)
        self.assertEqual(self.envelope_path().stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.exports.stat().st_mode & 0o777, 0o700)

    def test_unqualified_versions_record_totals_unknown_and_send_nothing(self):
        self.session()
        self.enable()
        self.run_hook(qualified=False)
        envelope = self.envelope()
        self.assertEqual(envelope["last_result"]["code"], "totals_unknown")
        self.assertIsNone(envelope["export_state"])
        self.assertEqual((FakeConnection.created, FakeConnection.posts), ([], []))

    def test_pending_is_saved_before_post_and_a_crash_leaves_it_uncertain(self):
        self.session()
        self.enable()
        seen = []

        def crash(planned, *, connection):
            stored = self.envelope()["export_state"]["records"]
            seen.append(stored[-1]["outcome"])
            self.assertEqual(stored[-1]["export_id"], planned["record"]["export_id"])
            raise KeyboardInterrupt

        with patch.object(export, "deliver", side_effect=crash), self.assertRaises(KeyboardInterrupt):
            self.run_hook()
        self.assertEqual(seen, ["pending"])
        first = self.envelope()["export_state"]["records"][0]["export_id"]
        with patch.object(export, "deliver", side_effect=AssertionError("resent")) as deliver:
            self.run_hook()
        deliver.assert_not_called()
        envelope = self.envelope()
        self.assertEqual(envelope["last_result"]["code"], "uncertain_pending")
        self.assertEqual(envelope["export_state"]["records"][0]["outcome"], "uncertain")
        status = hook.export_status(self.scope.repository_root, self.task_ref)["destinations"][0]
        self.assertEqual((status["destination_key"], status["revision"]), (self.key, envelope["revision"]))
        resolved = hook.resolve_export(self.scope.repository_root, dict(self.task_ref, expected_revision=envelope["revision"],
            destination_key=self.key, export_id=first, resolution="missing"))
        self.assertEqual(resolved["destinations"][0]["records"][0]["outcome"], "rejected")
        self.run_hook()
        records = self.envelope()["export_state"]["records"]
        self.assertEqual([record["outcome"] for record in records], ["rejected", "accepted"])
        self.assertNotEqual(records[1]["export_id"], first)
        self.assertEqual(len(FakeConnection.posts), 1)
        self.assertEqual(FakeConnection.posts[0][1]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["spanId"],
                         records[1]["export_id"])

    def test_deliver_failure_after_pending_keeps_it_and_never_resends(self):
        self.session()
        self.enable()

        def failing(planned, *, connection):
            self.assertEqual(self.envelope()["export_state"]["records"][-1]["outcome"], "pending")
            raise RuntimeError("PRIVATE delivery failure")

        with patch.object(export, "deliver", side_effect=failing) as deliver:
            self.run_hook()
        deliver.assert_called_once()
        envelope = self.envelope()
        self.assertEqual(envelope["last_result"]["code"], "hook_failed")
        self.assertEqual([record["outcome"] for record in envelope["export_state"]["records"]], ["pending"])
        self.assertNotIn("PRIVATE", self.envelope_path().read_text())
        with patch.object(export, "deliver", side_effect=AssertionError("resent")) as deliver:
            self.run_hook()
        deliver.assert_not_called()
        again = self.envelope()
        self.assertEqual(again["last_result"]["code"], "uncertain_pending")
        self.assertEqual([record["outcome"] for record in again["export_state"]["records"]], ["uncertain"])
        self.assertEqual((FakeConnection.posts, len(FakeConnection.created)), ([], 1))

    def test_deliver_sees_pending_on_disk_and_outcome_is_saved_after(self):
        self.session()
        self.enable()
        actual = export.deliver

        def observed(planned, *, connection):
            self.assertEqual(self.envelope()["export_state"]["records"][-1]["outcome"], "pending")
            return actual(planned, connection=connection)

        for outcome in ("rejected", "uncertain"):
            FakeConnection.outcome = outcome
            with self.subTest(outcome=outcome), patch.object(export, "deliver", side_effect=observed):
                self.run_hook()
                self.assertEqual(self.envelope()["last_result"]["code"], outcome)
                self.assertEqual(self.envelope()["export_state"]["records"][-1]["outcome"], outcome)
        self.assertEqual(len(FakeConnection.posts), 2)
        self.run_hook()
        self.assertEqual(self.envelope()["last_result"]["code"], "uncertain_pending")
        self.assertEqual(len(FakeConnection.posts), 2)

    def test_landed_resolution_counts_the_record_and_conflicts_are_refused(self):
        self.session()
        self.enable()
        FakeConnection.outcome = "uncertain"
        self.run_hook()
        envelope = self.envelope()
        export_id = envelope["export_state"]["records"][0]["export_id"]
        request = dict(self.task_ref, expected_revision=envelope["revision"] - 1, destination_key=self.key,
                       export_id=export_id, resolution="landed")
        with self.assertRaisesRegex(settings.SettingsError, r"\Aobservability_revision_conflict\Z"):
            hook.resolve_export(self.scope.repository_root, request)
        for bad in (dict(request, resolution="maybe"), dict(request, export_id="PRIVATE"), dict(request, extra=1),
                    dict(request, destination_key="0" * 31)):
            with self.subTest(bad=bad), self.assertRaisesRegex(settings.SettingsError, r"\Ainvalid_observability_request\Z"):
                hook.resolve_export(self.scope.repository_root, bad)
        result = hook.resolve_export(self.scope.repository_root, dict(request, expected_revision=envelope["revision"]))
        self.assertEqual(result["destinations"][0]["exported_totals"], USAGE)
        with self.assertRaisesRegex(settings.SettingsError, r"\Aobservability_export_not_resolvable\Z"):
            hook.resolve_export(self.scope.repository_root, dict(request, expected_revision=envelope["revision"] + 1))
        self.run_hook()
        self.assertEqual(self.envelope()["last_result"]["code"], "nothing_to_send")

    def test_unresolved_participants_block_upload(self):
        self.session()
        self.enable()
        record = dict(id="assignment-1", owner=dict(scope_id=state._scope_id(self.scope), **self.task_ref),
                      data=dict(role="backend-specialist", observation=None))
        with ledger([record]), \
                patch.object(codex_usage, "acquire_task_observations", side_effect=AssertionError("read")):
            self.run_hook()
        self.assertEqual(self.envelope()["last_result"]["code"], "participants_unresolved")
        self.assertIsNone(self.envelope()["export_state"])
        self.assertEqual(FakeConnection.posts, [])

    def test_credentials_unavailable_send_nothing(self):
        self.session()
        self.enable()
        with patch.dict(os.environ, {"LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": ""}), \
                patch.object(codex_usage, "QUALIFIED_VERSIONS", frozenset({VERSION})), \
                patch("http.client.HTTPSConnection", side_effect=AssertionError("network")):
            hook.after_completion(self.scope, self.request(), self.report())
        envelope = self.envelope()
        self.assertEqual(envelope["last_result"]["code"], "credentials_unavailable")
        self.assertIsNone(envelope["export_state"])

    def test_os_store_credential_failure_sends_nothing(self):
        self.session()
        self.enable(source="os-store")
        with patch.object(credentials, "load", side_effect=credentials.CredentialError("credential_store_unavailable")) as load:
            self.run_hook()
        load.assert_called_once_with(DESTINATION, PROJECT)
        self.assertEqual(self.envelope()["last_result"]["code"], "credentials_unavailable")
        self.assertEqual((FakeConnection.created, FakeConnection.posts), ([], []))

    def test_internal_failure_records_hook_failed_and_cancellation_propagates(self):
        self.session()
        self.enable()
        with patch.object(codex_usage, "acquire_task_observations", side_effect=RuntimeError("PRIVATE")):
            self.run_hook()
        self.assertEqual(self.envelope()["last_result"]["code"], "hook_failed")
        self.assertNotIn("PRIVATE", self.envelope_path().read_text())
        for exception in (KeyboardInterrupt(), SystemExit(9)):
            with self.subTest(exception=type(exception).__name__), \
                    patch.object(codex_usage, "acquire_task_observations", side_effect=exception), \
                    self.assertRaises(type(exception)):
                self.run_hook()

    def test_task_without_creation_time_records_totals_unknown(self):
        self.session()
        self.enable()
        del self.task["lifecycle"]
        with patch.object(codex_usage, "acquire_task_observations", side_effect=AssertionError("read")):
            self.run_hook()
        self.assertEqual(self.envelope()["last_result"]["code"], "totals_unknown")

    def test_export_status_is_read_only_and_empty_before_any_upload(self):
        self.enable()
        self.assertEqual(hook.export_status(self.scope.repository_root, self.task_ref),
                         dict(ok=True, task_ref=self.task_ref, destinations=[]))
        self.assertFalse(self.exports.exists())
        with self.assertRaisesRegex(settings.SettingsError, r"\Ainvalid_observability_request\Z"):
            hook.export_status(self.scope.repository_root, dict(self.task_ref, extra="PRIVATE"))

    def test_export_cli_actions_use_the_json_input(self):
        self.session()
        self.enable()
        FakeConnection.outcome = "uncertain"
        self.run_hook()
        envelope = self.envelope()
        export_id = envelope["export_state"]["records"][0]["export_id"]

        def invoke(action, request):
            stdin = io.TextIOWrapper(io.BytesIO(json.dumps(request).encode()))
            output = io.StringIO()
            with patch.object(sys, "path", list(sys.path)), patch.object(sys, "stdin", stdin), \
                    patch.object(sys, "stdout", output), \
                    patch.object(sys, "argv", ["observability.py", action, "--cwd", str(self.scope.repository_root)]):
                code = runpy.run_path(str(PACKAGE / "scripts/observability.py"))["main"]()
            return code, json.loads(output.getvalue())

        code, status = invoke("export-status", self.task_ref)
        self.assertEqual(code, 0)
        summary = status["destinations"][0]
        self.assertEqual(set(summary), {"destination_key", "revision", "records", "exported_totals", "last_result"})
        self.assertEqual(summary["records"], [dict(export_id=export_id, sequence=1, kind="total", outcome="uncertain")])
        self.assertEqual(summary["last_result"]["code"], "uncertain")
        code, resolved = invoke("resolve-export", dict(self.task_ref, expected_revision=summary["revision"],
            destination_key=self.key, export_id=export_id, resolution="landed"))
        self.assertEqual(code, 0)
        self.assertEqual(resolved["destinations"][0]["records"][0]["outcome"], "accepted")
        code, refused = invoke("resolve-export", dict(self.task_ref, expected_revision=summary["revision"],
            destination_key=self.key, export_id=export_id, resolution="landed"))
        self.assertEqual((code, refused), (1, dict(ok=False, error="observability_revision_conflict")))


class GateTests(HookFixture):
    """Each unmet condition stops the hook before any O3 read, credential, network or export file."""

    def assertNoEffect(self, report=None, request=None):
        with ExitStack() as stack:
            spies = [stack.enter_context(patch.object(codex_usage, "acquire_task_observations")),
                     stack.enter_context(patch.object(credentials, "load")),
                     stack.enter_context(patch.object(langfuse, "_Connection")),
                     stack.enter_context(patch("http.client.HTTPSConnection")),
                     stack.enter_context(patch.object(hook, "task_threads"))]
            hook.after_completion(self.scope, self.request() if request is None else request,
                                  self.report() if report is None else report)
        for spy in spies:
            spy.assert_not_called()
        self.assertFalse(self.exports.exists())

    def test_non_completion_or_unverified_results_do_nothing(self):
        self.session()
        self.enable(source="os-store")
        with patch.object(settings, "export_destination") as destination:
            for report, request in ((None, self.request("merge-evidence")), (self.report(verified=False), None),
                                    (self.report(ok=False), None), (self.report(admission_mode="qualification"), None),
                                    (None, {})):
                with self.subTest(report=report and {k: report[k] for k in ("ok", "verified", "admission_mode")},
                                  request=request):
                    self.assertNoEffect(report, request)
        destination.assert_not_called()

    def test_absent_settings_do_nothing(self):
        self.session()
        self.assertNoEffect()
        self.assertFalse((self.scope.runtime.state_dir / "observability").exists())

    def test_project_collection_off_does_nothing(self):
        self.session()
        self.enable(source="os-store", collection=False)
        self.assertNoEffect()

    def test_repository_missing_from_its_folder_record_does_nothing(self):
        self.session()
        self.enable(source="os-store", repositories=["other-repository"])
        self.assertNoEffect()

    def test_project_gate_runs_before_the_checkout_record_is_read(self):
        self.session()
        for name, arguments in (("collection off", dict(collection=False)),
                                ("repository missing", dict(repositories=["other-repository"]))):
            with self.subTest(name):
                shutil.rmtree(self.scope.runtime.state_dir / "observability", ignore_errors=True)
                self.enable(source="os-store", **arguments)
                original, opened = settings._guard, []

                def spy(scope, **kwargs):
                    opened.append("project" if kwargs.get("project") is not None else
                                  "export" if kwargs.get("export") is not None else "checkout")
                    return original(scope, **kwargs)

                with patch.object(settings, "_guard", side_effect=spy):
                    self.assertNoEffect()
                self.assertEqual(opened, ["project"])

    def test_checkout_connection_absent_or_off_does_nothing(self):
        self.session()
        self.project()
        self.assertNoEffect()
        settings.configure(self.scope, dict(expected_revision=0, destination=DESTINATION, langfuse_project_id=PROJECT,
                                            credential_source="os-store"))
        self.assertNoEffect()


class ThreadTests(HookFixture):
    def record(self, identifier, role, observation=None, **owner):
        return dict(id=identifier, owner=dict(scope_id=state._scope_id(self.scope), **self.task_ref) | owner,
                    data=dict(role=role, observation=observation))

    def history(self, *workers):
        return [(None, dict(receipt=dict(worker_id=worker))) for worker in workers]

    def threads(self, records, histories=None, collected=None):
        from runtime import evidence
        histories, original = histories or {}, evidence._read
        with ledger(records), \
                patch("runtime.delegation._worker_history", side_effect=lambda scope, reference: histories[reference]), \
                patch("runtime.evidence._read", side_effect=lambda scope, reference, **kwargs:
                      collected[reference] if type(reference) is str else original(scope, reference, **kwargs)):
            return hook.task_threads(self.scope, self.task)

    def test_coordinator_only_task_uses_its_session(self):
        self.assertEqual(hook.task_threads(self.scope, self.task), [dict(thread_id=SESSION, role="coordinator")])

    def test_workers_and_reviewers_are_listed_once_and_others_are_ignored(self):
        records = [self.record("w1", "backend-specialist", "obs-1"), self.record("r1", "code-reviewer", "obs-2"),
                   self.record("c1", "check"), self.record("o1", "orchestrator"),
                   self.record("x1", "backend-specialist", "obs-3", task_id="other-task")]
        self.task["evidence"] = dict(attempts=[dict(id="r1", kind="review", collected="col-1"),
                                               dict(id="r2", kind="review", collected="col-2"),
                                               dict(id="k1", kind="check")])
        result = self.threads(records, {"obs-1": self.history("worker-a", "worker-a"), "obs-2": self.history("reviewer-b")},
                              {"col-1": dict(receipt=dict(reviewer_id="reviewer-b")),
                               "col-2": dict(receipt=dict(reviewer_id="reviewer-c"))})
        self.assertEqual(result, [dict(thread_id=SESSION, role="coordinator"),
                                  dict(thread_id="worker-a", role="backend-specialist"),
                                  dict(thread_id="reviewer-b", role="code-reviewer"),
                                   dict(thread_id="reviewer-c", role="unknown")])

    def test_stored_specialist_and_legacy_roles_remain_verbatim(self):
        for role in ("reviewer", "review", "repair", "worker", "code-reviewer", "security-reviewer"):
            with self.subTest(role=role):
                result = self.threads([self.record("stored", role, "obs")],
                                      {"obs": self.history("stored-thread")})
                self.assertEqual(result[-1], dict(thread_id="stored-thread", role=role))

    def test_known_specialist_wins_over_duplicate_unlinked_receipt(self):
        self.task["evidence"] = dict(attempts=[dict(id="unlinked", kind="review", collected="receipt")])
        result = self.threads([self.record("linked", "security-reviewer", "obs")],
                              {"obs": self.history("same-thread")},
                              {"receipt": dict(receipt=dict(reviewer_id="same-thread"))})
        self.assertEqual(result, [dict(thread_id=SESSION, role="coordinator"),
                                  dict(thread_id="same-thread", role="security-reviewer")])

    def test_unknown_specialist_keeps_usage_and_complete_thread_coverage(self):
        from runtime import observability_accounting
        self.task["evidence"] = dict(attempts=[dict(id="unlinked", kind="review", collected="receipt")])
        threads = self.threads([], collected={"receipt": dict(receipt=dict(reviewer_id="legacy-thread"))})
        self.session()
        self.session(thread="legacy-thread")
        observations = codex_usage.acquire_task_observations(threads,
            started_at=datetime.fromtimestamp(self.started, timezone.utc),
            cutoff_at=datetime.fromtimestamp(self.started + 120, timezone.utc), deadline=time.monotonic() + 5)
        result = observability_accounting.reconcile_task(observations, verification=self.report())
        self.assertEqual(result["coverage"], "complete")
        self.assertEqual(result["totals"]["total_tokens"], 200)
        self.assertIn(dict(thread_id="legacy-thread", role="unknown"), result["counted_threads"])

    def test_unresolvable_worker_or_reviewer_threads_return_none(self):
        cases = (([self.record("w1", "backend-specialist")], {}, []),
                 ([self.record("w1", "backend-specialist", "obs-1")], {"obs-1": self.history("a", "b")}, []),
                 ([self.record("w1", "backend-specialist", "obs-1")], {"obs-1": self.history("PRIVATE/path")}, []),
                 ([], {}, [dict(id="r9", kind="review")]),
                 ([self.record("r1", "code-reviewer")], {}, [dict(id="r1", kind="review")]))
        for records, histories, attempts in cases:
            self.task["evidence"] = dict(attempts=attempts)
            with self.subTest(records=[r["id"] for r in records], attempts=attempts):
                self.assertIsNone(self.threads(records, histories, {}))


class ProducedHistoryTests(HookFixture):
    """Worker histories stored as real artifacts in the shape `delegation.observe_assignment` writes, read through
    the real `delegation._worker_history` and `evidence._read`; only the task's ledger query is replaced."""

    def receipt(self, worker, turn="turn-1"):
        # Field set of `delegation._worker_receipt`.
        return dict(version=2, mechanism="codex-native-collab-call", attempt_id="w1", task_name="w1-task",
            agent_path=None, native_version="0.167.0", client_surface="codex-cli", originator="codex_exec",
            parent_model_provider="openai", worker_model_provider="openai", parent_session_id=SESSION,
            parent_turn_id="parent-turn", spawn_item_id="spawn-1", worker_id=worker, worker_turn_id=turn,
            turn_status="completed", result_item_id="item-1", checkout=str(self.scope.repository_root),
            assignment_digest="a" * 64, candidate_digest="c" * 64, requested_model="gpt-5.5", requested_effort="high",
            configured_model="gpt-5.5", configured_effort="high", spawn_observed_model="gpt-5.5",
            spawn_observed_effort="high", execution_model="gpt-5.5", execution_effort="high",
            descendants_stopped=False, prompt_delivery="unverified", result_digest="d" * 64)

    def observe(self, receipt, previous=None):
        """One observation as `observe_assignment` stores it, including its conflict rule for `worker_id`."""
        from runtime import evidence
        owner = evidence._artifact_owner(self.scope, self.task)
        request = dict(parent_turn_id="parent-turn", spawn_item_id="spawn-1")
        incoming = evidence._artifact(self.scope, dict(receipt=receipt, result=None, report=None, error=None,
            request=request, raw=dict(receipt=receipt, result=None)), owner=owner)
        prior = evidence._read(self.scope, previous) if previous is not None else None
        stored, conflict, error = receipt, prior["conflict"] if prior else None, None
        if prior is not None and prior["receipt"]["worker_id"] != receipt["worker_id"]:
            stored, conflict = prior["receipt"], incoming
            error = "Contradictory native identity/result retained for independent reconciliation"
        value = dict(version=1, receipt=stored, result=None, report=None, error=error, request=request,
            incoming=incoming, previous=previous, conflict=conflict, revision=2 if prior is None else prior["revision"] + 1,
            status="uncertain" if error or conflict else "observed")
        return evidence._artifact(self.scope, value, owner=owner)

    def worker(self, observation):
        return dict(id="w1", owner=dict(scope_id=state._scope_id(self.scope), **self.task_ref),
                    data=dict(role="backend-specialist", observation=observation))

    def test_observed_worker_history_resolves_its_thread(self):
        first = self.observe(self.receipt("worker-a"))
        later = self.observe(self.receipt("worker-a", "turn-2"), first)
        with ledger([self.worker(later)]):
            self.assertEqual(hook.task_threads(self.scope, self.task), [
                dict(thread_id=SESSION, role="coordinator"), dict(thread_id="worker-a", role="backend-specialist")])

    def test_conflicting_worker_identity_is_unresolved(self):
        first = self.observe(self.receipt("worker-a"))
        conflicted = self.observe(self.receipt("worker-b"), first)
        carried = self.observe(self.receipt("worker-a", "turn-2"), conflicted)
        for reference in (conflicted, carried):
            with self.subTest(newest=reference is carried), ledger([self.worker(reference)]):
                self.assertIsNone(hook.task_threads(self.scope, self.task))

    def test_conflicting_worker_identity_blocks_upload(self):
        self.session()
        self.enable()
        conflicted = self.observe(self.receipt("worker-b"), self.observe(self.receipt("worker-a")))
        with ledger([self.worker(conflicted)]), \
                patch.object(codex_usage, "acquire_task_observations", side_effect=AssertionError("read")):
            self.run_hook()
        self.assertEqual(self.envelope()["last_result"]["code"], "participants_unresolved")
        self.assertIsNone(self.envelope()["export_state"])
        self.assertEqual((FakeConnection.created, FakeConnection.posts), ([], []))


class CallerTests(HookFixture):
    """The `evidence verify` CLI keeps its exact output and exit code with the hook in place."""

    def invoke(self, report, request=None, *, hook_patch=None):
        raw = json.dumps(self.request() if request is None else request).encode()
        stdin = io.TextIOWrapper(io.BytesIO(raw))
        output = io.StringIO()
        args = [str(PACKAGE / "scripts/workflow.py"), "evidence", "verify", "--cwd", str(self.fixture.repo)]
        with ExitStack() as stack:
            stack.enter_context(patch.object(sys, "path", list(sys.path)))
            stack.enter_context(patch.object(sys, "argv", args))
            stack.enter_context(patch.object(sys, "stdin", stdin))
            stack.enter_context(patch.object(sys, "stdout", output))
            stack.enter_context(patch("runtime.evidence.verify_completion", return_value=deepcopy(report)))
            stack.enter_context(patch.object(codex_usage, "QUALIFIED_VERSIONS", frozenset({VERSION})))
            stack.enter_context(patch.object(langfuse, "_Connection", FakeConnection))
            if hook_patch is not None:
                stack.enter_context(hook_patch)
            code = runpy.run_path(args[0])["control_main"]()
        return code, output.getvalue().encode()

    def baseline(self, report, request=None):
        return self.invoke(report, request, hook_patch=patch.object(hook, "after_completion", return_value=None))

    def test_disabled_output_and_exit_code_are_identical_to_no_hook(self):
        self.session()
        for report in (self.report(), self.report(verified=False)):
            with self.subTest(verified=report["verified"]), \
                    patch.object(codex_usage, "acquire_task_observations") as read:
                self.assertEqual(self.invoke(report), self.baseline(report))
                read.assert_not_called()
        self.assertFalse(self.exports.exists())

    def test_enabled_upload_and_hook_failure_keep_output_and_exit_code(self):
        self.session()
        self.enable()
        report = self.report()
        expected = self.baseline(report)
        self.assertEqual(self.invoke(report), expected)
        self.assertEqual(self.envelope()["last_result"]["code"], "accepted")
        self.assertEqual(len(FakeConnection.posts), 1)
        self.assertNotIn(b"export", expected[1])
        failing = patch.object(codex_usage, "acquire_task_observations", side_effect=RuntimeError("PRIVATE"))
        self.assertEqual(self.invoke(report, hook_patch=failing), expected)
        self.assertEqual(self.envelope()["last_result"]["code"], "hook_failed")
        broken = patch.object(hook, "_run", side_effect=ValueError("PRIVATE"))
        self.assertEqual(self.invoke(report, hook_patch=broken), expected)

    def test_cancellation_and_system_exit_propagate_from_the_cli(self):
        self.session()
        self.enable()
        for exception in (KeyboardInterrupt(), SystemExit(7)):
            with self.subTest(exception=type(exception).__name__), self.assertRaises(type(exception)):
                self.invoke(self.report(), hook_patch=patch.object(
                    codex_usage, "acquire_task_observations", side_effect=exception))

    def test_hook_import_failure_keeps_output(self):
        report = self.report()
        expected = self.baseline(report)
        import builtins
        original = builtins.__import__

        def missing(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "runtime" and fromlist and "observability_hook" in fromlist:
                raise ImportError("PRIVATE")
            return original(name, globals, locals, fromlist, level)

        self.assertEqual(self.invoke(report, hook_patch=patch("builtins.__import__", side_effect=missing)), expected)


COLLECTION = ("collection", "native_collection_qualified", "collection_sources")


class CollectionStatusTests(HookFixture):
    """`collection` in Project and checkout reports says what the upload hook would do in this checkout now."""

    def checkout_path(self):
        return self.scope.runtime.state_dir / "observability" / (state._scope_id(self.scope) + ".json")

    def assert_collection(self, expected, *scopes):
        """Both statuses give `expected`; `enabled` exactly when `export_destination` returns a connection and the
        hook sends."""
        scopes = scopes or (self.scope, self.scope.repository_root)
        reports = [settings.project_status(scope) for scope in scopes]
        for report in reports:
            self.assertEqual(report["collection"], expected)
        for scope in scopes:
            if expected == "connection_unavailable":
                # The checkout record is status's own subject, so an unreadable one stays an error there.
                with self.assertRaises(settings.SettingsError) as caught:
                    settings.status(scope)
                self.assertEqual(str(caught.exception), reports[0]["checkout_connection"]["error"])
                continue
            checkout = settings.status(scope)
            self.assertEqual(checkout["control_scope"], "checkout")
            self.assertEqual({key: checkout[key] for key in COLLECTION}, {key: reports[0][key] for key in COLLECTION})
        try:
            connection = settings.export_destination(self.scope)
        except settings.SettingsError:
            connection = None
        self.assertEqual(expected == "enabled", connection is not None)
        self.session()
        self.run_hook()
        self.assertEqual(expected == "enabled", bool(FakeConnection.posts))
        return reports[0]

    def test_off_without_record_or_with_the_choice_off(self):
        self.assertIsNone(self.assert_collection("off")["checkout_connection"])
        self.enable(collection=False)
        self.assertIsNone(self.assert_collection("off")["checkout_connection"])

    def test_repository_not_selected(self):
        self.enable(repositories=["other-repository"])
        self.assertIsNone(self.assert_collection("repository_not_selected")["checkout_connection"])

    def test_connection_unavailable_keeps_the_fixed_error_report(self):
        self.enable()
        self.checkout_path().chmod(0o644)
        report = self.assert_collection("connection_unavailable")
        self.assertEqual(report["checkout_connection"], dict(status="unavailable", error="unsafe_observability_storage"))

    def test_connection_not_enabled_without_record_or_opt_in(self):
        self.project()
        report = self.assert_collection("connection_not_enabled")
        self.assertEqual(report["checkout_connection"], dict(configured=False, desired_opt_in=False))
        settings.configure(self.scope, dict(expected_revision=0, destination=DESTINATION, langfuse_project_id=PROJECT))
        report = self.assert_collection("connection_not_enabled")
        self.assertEqual(report["checkout_connection"], dict(configured=True, desired_opt_in=False))

    def test_runtime_unavailable_without_current_qualified_scope(self):
        self.enable()
        broken = patch.object(startup, "acquire_project_entry", side_effect=startup.StartupError("qualification unavailable"))
        with broken:
            report = self.assert_collection("runtime_unavailable")
        self.assertEqual(report["checkout_connection"], dict(configured=True, desired_opt_in=True))
        (self.scope.runtime.state_dir / "runtime.sqlite3").write_bytes(b"broken runtime database")
        self.assert_collection("runtime_unavailable", self.scope.repository_root)

    def test_enabled_and_operation_reports_use_the_same_computation(self):
        self.enable(collection=False)
        configured = settings.configure_project(self.scope, dict(expected_revision=1,
            repository_ids=[self.scope.repository_id], desired_collection=True))
        self.assertEqual(configured["collection"], "enabled")
        report = self.assert_collection("enabled")
        self.assertEqual(report["checkout_connection"], dict(configured=True, desired_opt_in=True))
        self.assertEqual(settings.disable_project(self.scope, dict(expected_revision=2))["collection"], "off")

    def test_qualified_sources_follow_the_adapter(self):
        for versions, sources in ((frozenset({"0.168.0", "0.167.0"}), ["codex 0.167.0", "codex 0.168.0"]),
                                  (frozenset(), [])):
            with self.subTest(versions=versions), patch.object(codex_usage, "QUALIFIED_VERSIONS", versions):
                for report in (settings.project_status(self.scope), settings.status(self.scope)):
                    self.assertEqual((report["native_collection_qualified"], report["collection_sources"]),
                                     (bool(versions), sources))

    def test_gates_stop_before_later_records(self):
        loaded, acquired = [], []
        original_load, original_acquire = settings._Store.load, startup.acquire_project_entry
        def load(store):
            loaded.append(store.name)
            return original_load(store)
        def acquire(*args, **kwargs):
            acquired.append(True)
            return original_acquire(*args, **kwargs)
        checkout = state._scope_id(self.scope) + ".json"
        def observe(*operations):
            loaded.clear(), acquired.clear()
            with patch.object(settings._Store, "load", load), patch.object(startup, "acquire_project_entry", acquire):
                for operation in operations:
                    operation()
            return list(loaded), bool(acquired)
        status, export = (lambda: settings.project_status(self.scope.repository_root),
                          lambda: settings.export_destination(self.scope))
        self.assertEqual(observe(status), ([], False))
        self.enable(repositories=["other-repository"], opt_in=False)
        names, runtime = observe(status)
        self.assertEqual((checkout in names, runtime), (False, False))
        self.assertNotIn(checkout, observe(export)[0])
        settings.disable_project(self.scope, dict(expected_revision=1))
        self.assertNotIn(checkout, observe(status)[0])
        project = json.loads((self.scope.runtime.state_dir / "observability" /
                              ("project-" + self.scope.project_id + ".json")).read_text())
        settings.configure_project(self.scope, dict(expected_revision=project["revision"],
            repository_ids=[self.scope.repository_id], desired_collection=True))
        names, runtime = observe(status)
        self.assertIn(checkout, names)
        self.assertFalse(runtime)

    def test_status_reads_no_credentials_network_or_creates_files(self):
        self.enable()
        def snapshot():
            return {str(path): (path.lstat().st_mode, path.read_bytes() if path.is_file() else None)
                    for root in (self.scope.runtime.state_dir, self.scope.repository_root, self.codex_home)
                    for path in root.rglob("*")}
        before = snapshot()
        with ExitStack() as stack:
            for target in ("runtime.langfuse.verify_project", "runtime.langfuse.synthetic_probe",
                           "runtime.langfuse._Connection", "runtime.observability_credentials.load",
                           "runtime.observability_credentials.check_available"):
                stack.enter_context(patch(target, side_effect=AssertionError("external access")))
            for scope in (self.scope, self.scope.repository_root):
                self.assertEqual(settings.project_status(scope)["collection"], "enabled")
                self.assertEqual(settings.status(scope)["collection"], "enabled")
        self.assertEqual(snapshot(), before)

    def test_unreadable_project_record_keeps_checkout_status(self):
        self.enable()
        expected = settings.status(self.scope)
        project = self.scope.runtime.state_dir / "observability" / ("project-" + self.scope.project_id + ".json")
        original = project.read_bytes()
        for damage, code in ((lambda: project.chmod(0o644), "unsafe_observability_storage"),
                             (lambda: project.write_text('{"version":1,"PRIVATE":1}'), "invalid_observability_configuration")):
            project.write_bytes(original)
            project.chmod(0o600)
            damage()
            with self.subTest(code=code):
                for scope in (self.scope, self.scope.repository_root):
                    report = settings.status(scope)
                    self.assertEqual(report, dict(expected, collection="project_unavailable"))
                    self.assertNotIn("PRIVATE", json.dumps(report))
                    with self.assertRaises(settings.SettingsError) as caught:
                        settings.project_status(scope)
                    self.assertEqual(str(caught.exception), code)
                with self.assertRaises(settings.SettingsError):
                    settings.export_destination(self.scope)
        self.session()
        self.run_hook()
        self.assertEqual(FakeConnection.posts, [])

    def test_checkout_operation_reports_use_the_same_computation(self):
        def same(report, expected):
            self.assertEqual(report["collection"], expected)
            current = settings.status(self.scope)
            self.assertEqual({key: report[key] for key in COLLECTION}, {key: current[key] for key in COLLECTION})
        self.project()
        same(settings.configure(self.scope, dict(expected_revision=0, destination=DESTINATION,
                                                 langfuse_project_id=PROJECT)), "connection_not_enabled")
        with patch("runtime.langfuse.verify_project", return_value=None):
            same(settings.enable(self.scope, dict(expected_revision=1)), "enabled")
        with patch("runtime.langfuse.synthetic_probe", return_value=dict(verified=True)):
            same(settings.probe(self.scope, dict(expected_revision=2)), "enabled")
        same(settings.disable(self.scope, dict(expected_revision=2)), "connection_not_enabled")
        same(settings.configure(self.scope, dict(expected_revision=3, destination=DESTINATION, langfuse_project_id=PROJECT,
                                                 credential_source="os-store")), "connection_not_enabled")
        with patch("runtime.observability_credentials.forget", return_value=None):
            same(settings.forget_credentials(self.scope, dict(expected_revision=4)), "connection_not_enabled")
        with patch("runtime.observability_credentials.check_available", return_value=None), \
                patch("runtime.langfuse.verify_project", return_value=None), \
                patch("runtime.observability_credentials.store", return_value=None):
            same(settings.store_credentials(self.scope, dict(expected_revision=5),
                                            lambda: ("pk-lf-synthetic", "sk-lf-synthetic")), "connection_not_enabled")
        settings.disable_project(self.scope, dict(expected_revision=1))
        same(settings.disable(self.scope, dict(expected_revision=6)), "off")

    def test_checkout_status_reads_each_record_once(self):
        loaded, acquired = [], []
        original_load, original_acquire = settings._Store.load, startup.acquire_project_entry
        def load(store):
            loaded.append(store.name)
            return original_load(store)
        def acquire(*args, **kwargs):
            acquired.append(True)
            return original_acquire(*args, **kwargs)
        checkout, project = state._scope_id(self.scope) + ".json", "project-" + self.scope.project_id + ".json"
        def observe():
            loaded.clear(), acquired.clear()
            with patch.object(settings._Store, "load", load), patch.object(startup, "acquire_project_entry", acquire):
                report = settings.status(self.scope.repository_root)
            return report["collection"], sorted(loaded), bool(acquired)
        settings.configure(self.scope, dict(expected_revision=0, destination=DESTINATION, langfuse_project_id=PROJECT))
        self.assertEqual(observe(), ("off", [checkout], False))
        self.project(collection=False)
        self.assertEqual(observe(), ("off", sorted([checkout, project]), False))
        settings.configure_project(self.scope, dict(expected_revision=1, repository_ids=[self.scope.repository_id],
                                                    desired_collection=True))
        self.assertEqual(observe(), ("connection_not_enabled", sorted([checkout, project]), False))



class SettingsLimitTests(HookFixture):
    """Deferred O2 and integration review findings: repository IDs, record sizes, lookups and a busy lock."""

    def test_repository_ids_follow_the_canonical_record_rule(self):
        other = "Repo One.v2 (main)"
        report = settings.configure_project(self.scope, dict(expected_revision=0, desired_collection=True,
                                                             repository_ids=[self.scope.repository_id, other]))
        self.assertEqual(report["repository_ids"], sorted([self.scope.repository_id, other]))
        self.assertEqual(settings.project_status(self.scope)["repository_ids"], report["repository_ids"])
        for bad in ("", " ", "tab\there", "bell\x07", "x" * 129, 7):
            with self.subTest(bad=bad), self.assertRaisesRegex(settings.SettingsError, r"\Ainvalid_observability_request\Z"):
                settings.configure_project(self.scope, dict(expected_revision=1, desired_collection=True,
                                                            repository_ids=[self.scope.repository_id, bad]))

    def test_oversized_repository_list_is_refused_before_saving(self):
        ids = [self.scope.repository_id] + ["r%03d" % index + "x" * 124 for index in range(63)]
        with self.assertRaisesRegex(settings.SettingsError, r"\Aobservability_request_too_large\Z"):
            settings.configure_project(self.scope, dict(expected_revision=0, repository_ids=ids, desired_collection=True))
        self.assertEqual(list((self.scope.runtime.state_dir / "observability").glob("project-*.json")), [])

    def test_upload_state_with_250_records_saves_and_loads(self):
        from tests.test_observability_export import NOW, reconciled, usage
        state_ = None
        for revision in range(1, 251):
            planned = export.plan_export(reconciled(usage(input=60 + revision), revision=revision),
                                         export_state=state_, now_ns=NOW)
            state_ = export.record_outcome(planned["state"], planned["record"]["export_id"], "accepted")
        self.enable()
        with settings.export_store(self.scope, self.task_ref, self.key, write=True, create=True) as owner:
            self.assertIsNone(owner.load())
            hook._save(owner, None, self.task_ref, self.key, state_, "accepted")
        self.assertGreater(self.envelope_path().stat().st_size, 64 * 1024)
        with settings.export_store(self.scope, self.task_ref, self.key) as owner:
            self.assertEqual(export._state(owner.load()["export_state"]), state_)
        status = hook.export_status(self.scope.repository_root, self.task_ref)["destinations"][0]
        self.assertEqual(len(status["records"]), 250)
        self.assertEqual((settings.MAX_EXPORT, settings.MAX_CONFIG), (1024 * 1024, 8 * 1024))

    def counting(self):
        calls, current = [], startup.acquire_project_entry
        return calls, patch.object(startup, "acquire_project_entry", side_effect=lambda *args, **kwargs:
                                   calls.append(1) or current(*args, **kwargs))

    def test_enabled_hook_run_acquires_the_project_entry_once(self):
        self.session()
        self.enable()
        calls, counted = self.counting()
        with counted:
            self.run_hook()
        self.assertEqual(self.envelope()["last_result"]["code"], "accepted")
        self.assertEqual(len(FakeConnection.posts), 1)
        self.assertEqual(len(calls), 1)

    def test_changed_scope_is_still_refused(self):
        from dataclasses import replace
        self.session()
        self.enable()
        changed = replace(self.scope, accepted_scope_digest="0" * 64)
        with patch.object(startup, "_entry_scope", return_value=changed):
            self.run_hook()
        self.assertFalse(self.exports.exists())
        self.assertEqual(FakeConnection.posts, [])

    def test_settings_changes_still_refuse_a_changed_scope(self):
        from dataclasses import replace
        settings.configure(self.scope, dict(expected_revision=0, destination=DESTINATION, langfuse_project_id=PROJECT))
        directory = self.scope.runtime.state_dir / "observability"
        before = sorted((path.name, path.read_bytes()) for path in directory.iterdir())
        changed = replace(self.scope, accepted_scope_digest="0" * 64)
        with patch.object(startup, "_entry_scope", return_value=changed), \
                patch("runtime.langfuse.verify_project", side_effect=AssertionError("network")):
            for name, operation in (("enable", lambda: settings.enable(self.scope, dict(expected_revision=1))),
                                    ("configure_project", lambda: settings.configure_project(self.scope, dict(
                                        expected_revision=0, repository_ids=[self.scope.repository_id],
                                        desired_collection=True)))):
                with self.subTest(name), self.assertRaisesRegex(settings.SettingsError, r"\Aobservability_scope_changed\Z"):
                    operation()
        self.assertEqual(sorted((path.name, path.read_bytes()) for path in directory.iterdir()), before)

    def test_busy_upload_lock_records_nothing(self):
        self.session()
        self.enable()
        self.run_hook()
        before = self.envelope_path().read_bytes()
        original, calls = settings.export_store, []

        def busy_once(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise settings.SettingsError("observability_operation_busy")
            return original(*args, **kwargs)

        with patch.object(settings, "export_store", side_effect=busy_once):
            self.run_hook()
        self.assertEqual(self.envelope_path().read_bytes(), before)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(FakeConnection.posts), 1)

    def test_thread_id_with_underscore_is_unresolved_before_any_read(self):
        self.session()
        self.enable()
        self.task["session_id"] = "ses_opencode1"
        self.task_ref = dict(self.task_ref, session_id="ses_opencode1")
        with patch.object(codex_usage, "acquire_task_observations") as read, \
                patch.object(codex_usage, "read_thread_usage") as thread:
            self.run_hook()
        read.assert_not_called()
        thread.assert_not_called()
        self.assertEqual(self.envelope()["last_result"]["code"], "participants_unresolved")
        self.assertEqual(FakeConnection.posts, [])

if __name__ == "__main__":
    unittest.main()
