"""Langfuse export planning and delivery (D9 O5): pure bookkeeping and one status-aware POST, local fakes only."""

from copy import deepcopy
import json
import os
import re
import socket
import unittest
from unittest.mock import patch

import tests  # noqa: F401  (redirects HOME before the runtime is imported)
from runtime import langfuse
from runtime import observability_export as export
from runtime.adapters import codex_usage
from runtime.observability_accounting import reconcile_task
from tests.test_langfuse import PUBLIC_KEY, SECRET_KEY, protocol_server
from tests.test_observability_accounting import observations, thread, verification


KEYS = codex_usage.COUNTERS
TASK_ID = "task-private-7f3a"
SESSION_ID = "session-private-91bc"
THREAD_ID = "thread-private-c2d4"
PRIVATE_PATH = "/Users/someone/private-repo"
NOW = 1_735_689_600_000_000_000
METADATA = ("kind", "sequence", "correction_of", "coverage", "gap_codes", "cli_versions", "configured_models")


def usage(input=60, cached=20, write=0, output=40, reasoning=10, total=None):
    return dict(input_tokens=input, cached_input_tokens=cached, cache_write_input_tokens=write,
                output_tokens=output, reasoning_output_tokens=reasoning,
                total_tokens=input + output if total is None else total)


def reconciled(totals=None, *, revision=1, verified=True, mode="ordinary", coverage="complete", gaps=(),
               versions=("0.167.0",), settings=None):
    if settings is None:
        settings = [{"model": "gpt-5.5", "effort": "high", "basis": "configured"}]
    return {"task_ref": {"client": "codex", "session_id": SESSION_ID, "task_id": TASK_ID},
            "verified": verified, "admission_mode": mode, "reconciled_revision": revision,
            "totals": usage() if totals is None else totals,
            "counted_threads": [{"thread_id": THREAD_ID, "role": "coordinator"}],
            "coverage": coverage, "gaps": [{"thread_id": THREAD_ID, "code": code} for code in gaps],
            "source_versions": list(versions),
            "model_settings": [{"thread_id": THREAD_ID, "role": "coordinator", "model_settings": settings}]}


def attributes(body):
    span = body["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    return {item["key"]: item["value"]["stringValue"] for item in span["attributes"]}


def metadata(body):
    prefix = "langfuse.observation.metadata."
    return {key[len(prefix):]: value for key, value in attributes(body).items() if key.startswith(prefix)}


def details(body):
    return json.loads(attributes(body)["langfuse.observation.usage_details"])


class PlanTests(unittest.TestCase):
    def plan(self, value=None, state=None):
        return export.plan_export(reconciled() if value is None else value, export_state=state, now_ns=NOW)

    def send(self, value=None, state=None):
        planned = self.plan(value, state)
        self.assertEqual(planned["action"], "send")
        return planned

    def accepted(self, value=None, state=None):
        planned = self.send(value, state)
        return export.record_outcome(planned["state"], planned["record"]["export_id"], "accepted")

    def assertSkip(self, planned, reason):
        self.assertEqual(planned, {"action": "skip", "reason": reason})

    # Buckets

    def test_inclusive_counters_become_exclusive_usage_buckets(self):
        planned = self.send(reconciled(usage(input=100, cached=30, write=5, output=50, reasoning=20)))
        self.assertEqual(details(planned["body"]), {"input": 65, "input_cached_tokens": 30,
                                                    "input_cache_write_tokens": 5, "output": 30,
                                                    "output_reasoning_tokens": 20, "total": 150})
        self.assertEqual(export.usage_details(usage(input=100, cached=30, write=5, output=50, reasoning=20)),
                         details(planned["body"]))

    def test_negative_bucket_or_wrong_total_is_inconsistent(self):
        for totals in (usage(input=10, cached=8, write=5), usage(output=5, reasoning=6),
                       usage(total=101), usage(total=99)):
            with self.subTest(totals=totals):
                self.assertSkip(self.plan(reconciled(totals)), "usage_inconsistent")

    def test_delta_buckets_must_also_be_non_negative(self):
        state = self.accepted(reconciled(usage(input=60, cached=20), revision=1))
        # Every counter grows, but uncached input shrinks from 40 to 30: no exclusive bucket can express it.
        shifted = reconciled(usage(input=70, cached=40), revision=2)
        self.assertSkip(self.plan(shifted, state), "usage_inconsistent")

    def test_zero_valued_buckets_are_consistent(self):
        planned = self.send(reconciled(usage(input=0, cached=0, write=0, output=1, reasoning=0)))
        self.assertEqual(details(planned["body"])["total"], 1)

    # First upload and adjustments

    def test_first_plan_creates_pending_total_and_new_state(self):
        value = reconciled(revision=4)
        planned = self.send(value)
        self.assertEqual(set(planned), {"action", "record", "state", "body"})
        record, state = planned["record"], planned["state"]
        self.assertEqual(record["kind"], "total")
        self.assertEqual(record["sequence"], 1)
        self.assertIsNone(record["correction_of"])
        self.assertEqual(record["outcome"], "pending")
        self.assertEqual(record["delta"], usage())
        self.assertEqual(record["reconciled_revision"], 4)
        self.assertRegex(record["export_id"], r"\A[0-9a-f]{16}\Z")
        self.assertRegex(state["correlation_id"], r"\A[0-9a-f]{32}\Z")
        self.assertEqual(state["revision"], 1)
        self.assertIsNone(state["reconciled_revision"])
        self.assertEqual(state["exported_totals"], dict.fromkeys(KEYS, 0))
        self.assertEqual(state["records"], [record])
        self.assertIsNot(state["records"][0], record)

    def test_hundred_corrected_to_hundred_twenty_adds_one_adjustment_of_twenty(self):
        first = self.send(reconciled(usage(input=60, cached=20, output=40, reasoning=10), revision=1))
        state = export.record_outcome(first["state"], first["record"]["export_id"], "accepted")
        self.assertEqual(state["exported_totals"]["total_tokens"], 100)
        self.assertEqual(state["reconciled_revision"], 1)
        second = self.send(reconciled(usage(input=80, cached=20, output=40, reasoning=10), revision=2), state)
        record = second["record"]
        self.assertEqual(record["kind"], "adjustment")
        self.assertEqual(record["sequence"], 2)
        self.assertEqual(record["correction_of"], first["record"]["export_id"])
        self.assertEqual(record["delta"], dict.fromkeys(KEYS, 0) | {"input_tokens": 20, "total_tokens": 20})
        self.assertEqual(second["state"]["correlation_id"], state["correlation_id"])
        self.assertEqual(second["state"]["revision"], state["revision"] + 1)
        self.assertNotEqual(record["export_id"], first["record"]["export_id"])
        self.assertEqual(metadata(second["body"])["correction_of"], first["record"]["export_id"])
        self.assertEqual(details(second["body"]), {"input": 20, "input_cached_tokens": 0,
                                                   "input_cache_write_tokens": 0, "output": 0,
                                                   "output_reasoning_tokens": 0, "total": 20})
        final = export.record_outcome(second["state"], record["export_id"], "accepted")
        accepted = [item["delta"] for item in final["records"] if item["outcome"] == "accepted"]
        self.assertEqual(sum(delta["total_tokens"] for delta in accepted), 120)
        self.assertEqual(final["exported_totals"], usage(input=80, cached=20, output=40, reasoning=10))
        self.assertEqual(final["reconciled_revision"], 2)
        self.assertIsNone(self.plan(reconciled(usage(input=80, cached=20, output=40, reasoning=10), revision=3),
                                    final))

    def test_inputs_are_never_mutated(self):
        value, state = reconciled(revision=2), self.accepted(reconciled(usage(input=50), revision=1))
        value_copy, state_copy = deepcopy(value), deepcopy(state)
        planned = self.send(value, state)
        self.assertEqual((value, state), (value_copy, state_copy))
        pending = deepcopy(planned["state"])
        for result in (export.record_outcome(planned["state"], planned["record"]["export_id"], "uncertain"),
                       export.recover_pending(planned["state"])):
            self.assertEqual(planned["state"], pending)
            self.assertIsNot(result, planned["state"])
        uncertain = export.recover_pending(planned["state"])
        copy = deepcopy(uncertain)
        export.resolve_uncertain(uncertain, planned["record"]["export_id"], "landed")
        self.assertEqual(uncertain, copy)

    def test_real_reconciliation_output_is_accepted(self):
        observed = observations(thread(usage=usage()))
        value = reconcile_task(observed, verification=verification(revision=3))
        planned = self.send(value)
        self.assertEqual(planned["record"]["reconciled_revision"], 3)
        self.assertEqual(metadata(planned["body"])["configured_models"], "gpt-5.5/high")

    # Nothing new and skips

    def test_all_zero_delta_is_none_whatever_the_revision(self):
        state = self.accepted(reconciled(revision=5))
        for revision in (1, 5, 6):
            with self.subTest(revision=revision):
                self.assertIsNone(self.plan(reconciled(revision=revision), state))

    def test_zero_usage_first_plan_is_none(self):
        self.assertIsNone(self.plan(reconciled(usage(input=0, cached=0, output=0, reasoning=0))))

    def test_unverified_or_qualification_results_are_not_exportable(self):
        for value in (reconciled(verified=False), reconciled(mode="qualification")):
            with self.subTest(mode=value["admission_mode"], verified=value["verified"]):
                self.assertSkip(self.plan(value), "not_exportable")

    def test_any_unknown_total_blocks_export(self):
        for key in KEYS:
            with self.subTest(key=key):
                self.assertSkip(self.plan(reconciled(usage() | {key: None})), "totals_unknown")

    def test_pending_or_uncertain_record_blocks_export(self):
        planned = self.send(reconciled(revision=1))
        self.assertSkip(self.plan(reconciled(revision=2), planned["state"]), "uncertain_pending")
        uncertain = export.record_outcome(planned["state"], planned["record"]["export_id"], "uncertain")
        self.assertSkip(self.plan(reconciled(revision=2), uncertain), "uncertain_pending")

    def test_stale_reconciliation_is_refused(self):
        state = self.accepted(reconciled(revision=5))
        for revision in (4, 5):
            with self.subTest(revision=revision):
                self.assertSkip(self.plan(reconciled(usage(input=80), revision=revision), state),
                                "stale_reconciliation")
        self.assertEqual(self.send(reconciled(usage(input=80), revision=6), state)["record"]["kind"], "adjustment")

    def test_negative_delta_is_not_exported(self):
        state = self.accepted(reconciled(usage(input=80), revision=1))
        self.assertSkip(self.plan(reconciled(usage(input=60), revision=2), state), "negative_delta")

    def test_rejected_first_upload_can_be_planned_again_at_same_revision(self):
        first = self.send(reconciled(revision=3))
        state = export.record_outcome(first["state"], first["record"]["export_id"], "rejected")
        self.assertIsNone(state["reconciled_revision"])
        self.assertEqual(state["exported_totals"], dict.fromkeys(KEYS, 0))
        again = self.send(reconciled(revision=3), state)
        self.assertEqual(again["record"]["kind"], "total")
        self.assertEqual(again["record"]["sequence"], 2)
        self.assertIsNone(again["record"]["correction_of"])
        self.assertEqual(again["record"]["delta"], usage())
        self.assertNotEqual(again["record"]["export_id"], first["record"]["export_id"])
        self.assertEqual(again["state"]["correlation_id"], first["state"]["correlation_id"])
        self.assertEqual([item["outcome"] for item in again["state"]["records"]], ["rejected", "pending"])

    # Restart and resolution

    def test_restart_turns_pending_into_uncertain_and_blocks_planning(self):
        planned = self.send(reconciled(revision=1))
        recovered = export.recover_pending(planned["state"])
        self.assertEqual(recovered["records"][0]["outcome"], "uncertain")
        self.assertEqual(recovered["revision"], planned["state"]["revision"] + 1)
        self.assertSkip(self.plan(reconciled(revision=1), recovered), "uncertain_pending")
        self.assertIsNone(export.recover_pending(None))
        settled = export.record_outcome(planned["state"], planned["record"]["export_id"], "accepted")
        self.assertEqual(export.recover_pending(settled), settled)

    def test_missing_resolution_allows_a_new_export_id(self):
        planned = self.send(reconciled(revision=1))
        uncertain = export.recover_pending(planned["state"])
        missing = export.resolve_uncertain(uncertain, planned["record"]["export_id"], "missing")
        self.assertEqual(missing["records"][0]["outcome"], "rejected")
        self.assertEqual(missing["exported_totals"], dict.fromkeys(KEYS, 0))
        again = self.send(reconciled(revision=1), missing)
        self.assertNotEqual(again["record"]["export_id"], planned["record"]["export_id"])
        self.assertEqual(again["record"]["kind"], "total")

    def test_landed_resolution_counts_the_record_once(self):
        planned = self.send(reconciled(revision=1))
        uncertain = export.recover_pending(planned["state"])
        landed = export.resolve_uncertain(uncertain, planned["record"]["export_id"], "landed")
        self.assertEqual(landed["records"][0]["outcome"], "accepted")
        self.assertEqual(landed["exported_totals"], usage())
        self.assertEqual(landed["reconciled_revision"], 1)
        self.assertIsNone(self.plan(reconciled(revision=2), landed))
        with self.assertRaisesRegex(ValueError, r"\AExport record cannot change\Z"):
            export.resolve_uncertain(landed, planned["record"]["export_id"], "landed")

    def test_settled_records_cannot_change(self):
        planned = self.send(reconciled(revision=1))
        export_id = planned["record"]["export_id"]
        for outcome in ("accepted", "rejected", "uncertain"):
            settled = export.record_outcome(planned["state"], export_id, outcome)
            for again in ("accepted", "rejected", "uncertain"):
                with self.subTest(outcome=outcome, again=again), \
                        self.assertRaisesRegex(ValueError, r"\AExport record cannot change\Z"):
                    export.record_outcome(settled, export_id, again)
        with self.assertRaisesRegex(ValueError, r"\AExport record cannot change\Z"):
            export.resolve_uncertain(planned["state"], export_id, "missing")

    def test_invalid_outcome_resolution_or_unknown_record_is_refused_without_echo(self):
        planned = self.send(reconciled(revision=1))
        export_id = planned["record"]["export_id"]
        cases = ((export.record_outcome, export_id, "pending", "Invalid export outcome"),
                 (export.record_outcome, export_id, "PRIVATE", "Invalid export outcome"),
                 (export.record_outcome, "PRIVATE", "accepted", "Unknown export record"),
                 (export.resolve_uncertain, export_id, "PRIVATE", "Invalid export resolution"),
                 (export.resolve_uncertain, "0" * 15 + "1", "landed", "Unknown export record"))
        for function, identifier, value, message in cases:
            with self.subTest(function=function.__name__, message=message), \
                    self.assertRaisesRegex(ValueError, r"\A" + message + r"\Z") as raised:
                function(planned["state"], identifier, value)
            self.assertIsNone(raised.exception.__context__)

    # State and input validation

    def test_malformed_state_is_refused_with_fixed_message(self):
        first = self.send(reconciled(revision=1))
        accepted = export.record_outcome(first["state"], first["record"]["export_id"], "accepted")
        second = self.send(reconciled(usage(input=80), revision=2), accepted)
        good = export.record_outcome(second["state"], second["record"]["export_id"], "rejected")

        def changed(edit):
            value = deepcopy(good)
            edit(value)
            return value

        broken = [
            [], {}, "PRIVATE",
            changed(lambda s: s.update(extra="PRIVATE")),
            changed(lambda s: s.pop("records")),
            changed(lambda s: s.update(revision=True)),
            changed(lambda s: s.update(revision=-1)),
            changed(lambda s: s.update(correlation_id="PRIVATE")),
            changed(lambda s: s.update(correlation_id="0" * 32)),
            changed(lambda s: s.update(correlation_id=s["correlation_id"].upper())),
            changed(lambda s: s.update(reconciled_revision="1")),
            changed(lambda s: s["exported_totals"].update(total_tokens=101)),
            changed(lambda s: s["exported_totals"].pop("total_tokens")),
            changed(lambda s: s["records"][0].update(extra="PRIVATE")),
            changed(lambda s: s["records"][0].update(export_id="PRIVATE")),
            changed(lambda s: s["records"][1].update(export_id=s["records"][0]["export_id"])),
            changed(lambda s: s["records"][0].update(sequence=2)),
            changed(lambda s: s["records"][0].update(kind="PRIVATE")),
            changed(lambda s: s["records"][0].update(outcome="PRIVATE")),
            changed(lambda s: s["records"][0].update(correction_of=s["records"][1]["export_id"])),
            changed(lambda s: s["records"][1].update(correction_of=None)),
            changed(lambda s: s["records"][1].update(kind="total", correction_of=None)),
            changed(lambda s: s["records"][0]["delta"].update(input_tokens=-1)),
            changed(lambda s: s["records"][0]["delta"].update(input_tokens=1.5)),
            changed(lambda s: s["records"][0].update(outcome="pending")),
            changed(lambda s: s.update(reconciled_revision=None)),
            changed(lambda s: s["records"].reverse()),
        ]
        for index, state in enumerate(broken):
            with self.subTest(index=index):
                for call in (lambda: self.plan(reconciled(revision=3), state),
                             lambda: export.record_outcome(state, good["records"][1]["export_id"], "accepted"),
                             lambda: export.recover_pending(state),
                             lambda: export.resolve_uncertain(state, good["records"][1]["export_id"], "landed")):
                    with self.assertRaisesRegex(ValueError, r"\AInvalid export state\Z") as raised:
                        call()
                    self.assertNotIn("PRIVATE", str(raised.exception))
                    self.assertIsNone(raised.exception.__context__)
        self.assertEqual(self.send(reconciled(usage(input=80), revision=3), good)["record"]["sequence"], 3)

    def test_malformed_reconciliation_is_refused_with_fixed_message(self):
        def changed(edit):
            value = reconciled()
            edit(value)
            return value

        broken = [None, [], changed(lambda r: r.update(extra="PRIVATE")),
                  changed(lambda r: r.pop("totals")),
                  changed(lambda r: r.update(verified="true")),
                  changed(lambda r: r.update(reconciled_revision=True)),
                  changed(lambda r: r.update(reconciled_revision=-1)),
                  changed(lambda r: r["totals"].update(input_tokens=-1)),
                  changed(lambda r: r["totals"].update(input_tokens=60.0)),
                  changed(lambda r: r["totals"].pop("total_tokens")),
                  changed(lambda r: r.update(coverage="PRIVATE")),
                  changed(lambda r: r.update(gaps=[{"thread_id": None, "code": "PRIVATE"}])),
                  changed(lambda r: r.update(source_versions="0.167.0")),
                  changed(lambda r: r.update(model_settings=[{"model_settings": "PRIVATE"}]))]
        for index, value in enumerate(broken):
            with self.subTest(index=index), \
                    self.assertRaisesRegex(ValueError, r"\AInvalid task usage reconciliation\Z") as raised:
                export.plan_export(value, export_state=None, now_ns=NOW)
            self.assertNotIn("PRIVATE", str(raised.exception))

    def test_metadata_outside_fixed_patterns_is_refused(self):
        for value in (reconciled(versions=["0.167.0 PRIVATE"]), reconciled(versions=["0.167,0"]),
                      reconciled(settings=[{"model": "gpt 5 PRIVATE", "effort": "high"}]),
                      reconciled(settings=[{"model": "gpt-5.5", "effort": "hi,gh"}]),
                      reconciled(settings=[{"model": PRIVATE_PATH, "effort": "high"}])):
            with self.subTest(value=value["source_versions"]), \
                    self.assertRaisesRegex(ValueError, r"\AExport metadata refused\Z") as raised:
                self.plan(value)
            self.assertNotIn("PRIVATE", str(raised.exception))

    # OTLP body

    def test_body_has_exact_shape_and_attribute_allow_list(self):
        value = reconciled(revision=2, coverage="incomplete", gaps=("token_info_missing", "counter_missing",
                                                                    "token_info_missing"),
                           versions=("0.168.0", "0.167.0"),
                           settings=[{"model": "gpt-5.5", "effort": "high"}, {"model": None, "effort": None},
                                     {"model": "gpt-5.5", "effort": "high"}, {"model": "codex-mini", "effort": None}])
        planned = self.send(value)
        body, record = planned["body"], planned["record"]
        json.dumps(body)
        self.assertEqual(set(body), {"resourceSpans"})
        self.assertEqual(len(body["resourceSpans"]), 1)
        resource = body["resourceSpans"][0]
        self.assertEqual(set(resource), {"resource", "scopeSpans"})
        self.assertEqual(resource["resource"], {"attributes": [
            {"key": "service.name", "value": {"stringValue": langfuse.SERVICE_NAME}}]})
        self.assertEqual(len(resource["scopeSpans"]), 1)
        scope = resource["scopeSpans"][0]
        self.assertEqual(scope["scope"], {"name": "vibe-coding-workflow.observability"})
        self.assertEqual(len(scope["spans"]), 1)
        span = scope["spans"][0]
        self.assertEqual(set(span), {"traceId", "spanId", "name", "kind", "startTimeUnixNano", "endTimeUnixNano",
                                     "attributes"})
        self.assertEqual(span["traceId"], planned["state"]["correlation_id"])
        self.assertEqual(span["spanId"], record["export_id"])
        self.assertRegex(span["traceId"], r"\A[0-9a-f]{32}\Z")
        self.assertRegex(span["spanId"], r"\A[0-9a-f]{16}\Z")
        self.assertEqual((span["name"], span["kind"]), ("vibe-task-usage", 1))
        self.assertEqual((span["startTimeUnixNano"], span["endTimeUnixNano"]), (str(NOW), str(NOW + 1)))
        for item in span["attributes"]:
            self.assertEqual(set(item), {"key", "value"})
            self.assertEqual(set(item["value"]), {"stringValue"})
            self.assertIsInstance(item["value"]["stringValue"], str)
        found = attributes(body)
        self.assertEqual(len(found), len(span["attributes"]))
        self.assertEqual(set(found), {"langfuse.observation.type", "langfuse.trace.name",
                                      "langfuse.observation.usage_details"}
                         | {"langfuse.observation.metadata." + key for key in METADATA})
        self.assertEqual(found["langfuse.observation.type"], "generation")
        self.assertEqual(found["langfuse.trace.name"], "vibe-task-usage")
        self.assertEqual(metadata(body), {"kind": "total", "sequence": "1", "correction_of": "",
                                          "coverage": "incomplete", "gap_codes": "counter_missing,token_info_missing",
                                          "cli_versions": "0.167.0,0.168.0",
                                          "configured_models": "-/-,codex-mini/-,gpt-5.5/high"})
        serialized = json.dumps(body)
        for excluded in ("model.name", "model\"", "gen_ai", "cost", "user", "session", "llm."):
            self.assertNotIn(excluded, serialized)

    def test_body_carries_no_task_session_thread_or_path(self):
        value = reconciled()
        value["task_ref"]["client"] = PRIVATE_PATH
        value["counted_threads"][0]["role"] = PRIVATE_PATH
        serialized = json.dumps(self.send(value))
        for private in (TASK_ID, SESSION_ID, THREAD_ID, PRIVATE_PATH, "private"):
            self.assertNotIn(private, serialized)

    def test_now_defaults_to_current_time_and_must_be_valid(self):
        with patch.object(export.time, "time_ns", return_value=NOW + 5):
            planned = export.plan_export(reconciled(), export_state=None)
        span = planned["body"]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        self.assertEqual(span["startTimeUnixNano"], str(NOW + 5))
        for value in (0, -1, True, 1.5, "1"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, r"\AInvalid export time\Z"):
                export.plan_export(reconciled(), export_state=None, now_ns=value)

    def test_new_ids_skip_all_zero_and_existing_values(self):
        first = self.send(reconciled(revision=1))
        rejected = export.record_outcome(first["state"], first["record"]["export_id"], "rejected")
        values = iter(["0" * 16, first["record"]["export_id"], "0123456789abcdef"])
        with patch.object(export.secrets, "token_hex", side_effect=lambda size: next(values)):
            again = self.send(reconciled(revision=1), rejected)
        self.assertEqual(again["record"]["export_id"], "0123456789abcdef")
        values = iter(["0" * 32, "f" * 32, "1" * 16])
        with patch.object(export.secrets, "token_hex", side_effect=lambda size: next(values)):
            self.assertEqual(self.send()["state"]["correlation_id"], "f" * 32)


class DeliverTests(unittest.TestCase):
    def planned(self):
        return export.plan_export(reconciled(), export_state=None, now_ns=NOW)

    def connection(self, server, seconds=5):
        return langfuse._Connection(server.url, "lf-project", seconds, (PUBLIC_KEY, SECRET_KEY))

    def deliver(self, server, planned=None):
        planned = self.planned() if planned is None else planned
        result = export.deliver(planned, connection=self.connection(server))
        self.assertEqual(set(result), {"outcome"})
        serialized = json.dumps(result)
        for secret in (PUBLIC_KEY, SECRET_KEY, "PRIVATE", "Basic"):
            self.assertNotIn(secret, serialized)
        return result["outcome"]

    def posts(self, server):
        return [request for request in server.requests if request["method"] == "POST"]

    def test_accepted_posts_the_planned_body_once_with_ingestion_headers(self):
        with protocol_server() as server:
            planned = self.planned()
            for partial in ({}, {"rejectedSpans": 0}, {"rejectedSpans": "0"}):
                with self.subTest(partial=partial):
                    server.requests.clear()
                    server.partial = partial
                    self.assertEqual(self.deliver(server, planned), "accepted")
                    posts = self.posts(server)
                    self.assertEqual(len(posts), 1)
                    self.assertEqual(len(server.requests), 1)
                    self.assertEqual(posts[0]["path"], "/api/public/otel/v1/traces")
                    self.assertEqual(posts[0]["body"], planned["body"])
                    self.assertEqual(posts[0]["headers"]["X-Langfuse-Ingestion-Version"], "4")
                    self.assertEqual(posts[0]["headers"]["Content-Type"], "application/json")

    def test_two_hundred_without_partial_success_is_accepted(self):
        with protocol_server() as server:
            server.raw_response = b"{}"
            self.assertEqual(self.deliver(server), "accepted")

    def test_rejected_span_in_any_success_status_is_rejected(self):
        with protocol_server() as server:
            for status in (200, 207):
                for rejected in (1, "1", "2"):
                    with self.subTest(status=status, rejected=rejected):
                        server.requests.clear()
                        server.post_status = status
                        server.partial = {"rejectedSpans": rejected, "errorMessage": "PRIVATE"}
                        self.assertEqual(self.deliver(server), "rejected")
                        self.assertEqual(len(self.posts(server)), 1)

    def test_redirect_and_client_errors_are_rejected_without_retry(self):
        with protocol_server() as server:
            for status in (301, 302, 307, 400, 401, 403, 404, 413, 422):
                with self.subTest(status=status):
                    server.requests.clear()
                    server.post_status = status
                    self.assertEqual(self.deliver(server), "rejected")
                    self.assertEqual(len(server.requests), 1)

    def test_timeout_quota_and_server_errors_are_uncertain_without_retry(self):
        with protocol_server() as server:
            for status in (408, 429, 500, 502, 503, 504):
                with self.subTest(status=status):
                    server.requests.clear()
                    server.post_status = status
                    self.assertEqual(self.deliver(server), "uncertain")
                    self.assertEqual(len(server.requests), 1)

    def test_invalid_or_unreadable_acknowledgement_is_uncertain(self):
        with protocol_server() as server:
            cases = [dict(partial="PRIVATE"), dict(partial={"rejectedSpans": -1}),
                     dict(partial={"rejectedSpans": True}), dict(partial={"rejectedSpans": "one"}),
                     dict(partial={"rejectedSpans": 1.0}), dict(partial={"rejectedSpans": None}),
                     dict(raw_response=b"{PRIVATE"), dict(raw_response=b"[]"), dict(raw_response=b""),
                     dict(raw_response=b"\xff"), dict(raw_response=b'{"a":1,"a":2}'),
                     dict(raw_response=b"PRIVATE" * 23000), dict(content_type="text/html")]
            for case in cases:
                with self.subTest(case=sorted(case)):
                    server.requests.clear()
                    server.partial, server.raw_response, server.content_type = {}, None, "application/json"
                    for key, value in case.items():
                        setattr(server, key, value)
                    self.assertEqual(self.deliver(server), "uncertain")
                    self.assertEqual(len(server.requests), 1)

    def test_timeout_after_sending_is_uncertain(self):
        with protocol_server() as server, patch.object(langfuse, "REQUEST_SECONDS", .5):
            server.response_delay = 1.5
            self.assertEqual(self.deliver(server), "uncertain")
            self.assertEqual(len(self.posts(server)), 1)

    def test_connect_failure_before_sending_is_rejected(self):
        with protocol_server() as server:
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            listener.close()
            closed = langfuse._Connection("https://127.0.0.1:" + str(port), "lf-project", 5,
                                          (PUBLIC_KEY, SECRET_KEY))
            self.assertEqual(export.deliver(self.planned(), connection=closed), {"outcome": "rejected"})
            self.assertEqual(server.requests, [])

    def test_tls_failure_before_sending_is_rejected(self):
        with protocol_server() as server, patch.dict(os.environ):
            os.environ.pop("SSL_CERT_FILE")
            self.assertEqual(self.deliver(server), "rejected")
            self.assertEqual(server.requests, [])

    def test_expired_deadline_before_sending_is_rejected(self):
        with protocol_server() as server:
            expired = self.connection(server, seconds=0)
            self.assertEqual(export.deliver(self.planned(), connection=expired), {"outcome": "rejected"})
            self.assertEqual(server.requests, [])

    def test_send_failure_after_connect_is_uncertain(self):
        with protocol_server() as server, \
                patch.object(langfuse.http.client.HTTPSConnection, "request", side_effect=OSError("PRIVATE")):
            self.assertEqual(self.deliver(server), "uncertain")

    def test_cancellation_is_not_swallowed(self):
        with protocol_server() as server, \
                patch.object(langfuse.http.client.HTTPSConnection, "getresponse", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.deliver(server)

    def test_deliver_refuses_anything_but_a_send_plan_before_network(self):
        planned = self.planned()
        other = deepcopy(planned)
        other["body"]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["spanId"] = "0123456789abcdef"
        settled = deepcopy(planned)
        settled["record"]["outcome"] = "accepted"
        with patch("http.client.HTTPSConnection", side_effect=AssertionError("network")):
            fake = langfuse._Connection("https://cloud.langfuse.com", "lf-project", 5, (PUBLIC_KEY, SECRET_KEY))
            for value in (None, {"action": "skip", "reason": "totals_unknown"}, {"action": "send"},
                          other, settled, dict(planned, body={"resourceSpans": []})):
                with self.subTest(value=value if value is None else sorted(value)), \
                        self.assertRaisesRegex(ValueError, r"\AInvalid export plan\Z"):
                    export.deliver(value, connection=fake)

    def test_existing_request_behavior_is_unchanged(self):
        with protocol_server() as server:
            connection = self.connection(server)
            server.post_status = 503
            with self.assertRaises(langfuse.LangfuseError) as caught:
                connection.request("POST", "/api/public/otel/v1/traces", self.planned()["body"])
            self.assertEqual(str(caught.exception), "destination_rejected_request")


if __name__ == "__main__":
    unittest.main()
