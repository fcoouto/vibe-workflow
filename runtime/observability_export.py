"""Plan Langfuse exports of one task's settled usage and deliver each planned record once (D9 O5).

Langfuse observations are immutable and re-ingesting an ID duplicates it, so a task's usage is uploaded once as a
``total`` record and every later correction is a separate ``adjustment`` record carrying only the change. Langfuse
sums the records of a task, so its total reaches the corrected value without any upload being replaced.

``plan_export``, ``record_outcome``, ``recover_pending`` and ``resolve_uncertain`` are pure: they read no file,
state or network, never mutate their inputs and return new objects. The evidence owner stores the export state;
this module only transforms it. ``deliver`` performs exactly one POST and never retries.

Codex counters are treated as inclusive (provider semantics: ``input_tokens`` includes cached and cache-write
tokens, ``output_tokens`` includes reasoning tokens). This is not yet confirmed on real session files. Langfuse
stores a flat ``langfuse.observation.usage_details`` unchanged and treats every key as an exclusive bucket, so the
counters are split into exclusive buckets before upload and refused when they cannot be.

No model name or cost is sent: Langfuse would infer API prices, which are wrong for subscription use. The
configured model and effort travel as metadata only. Malformed input is refused with fixed messages that carry no
input value.
"""

from copy import deepcopy
import json
import re
import secrets
import time

from runtime.adapters.codex_usage import COUNTERS, GAP_CODES
from runtime.langfuse import SERVICE_NAME


SPAN_NAME = "vibe-task-usage"
SCOPE_NAME = "vibe-coding-workflow.observability"
OTLP_PATH = "/api/public/otel/v1/traces"
OUTCOMES = ("pending", "accepted", "rejected", "uncertain")
DELIVERY_OUTCOMES = ("accepted", "rejected", "uncertain")
_OPEN = ("pending", "uncertain")
_KINDS = ("total", "adjustment")
_RESOLUTIONS = {"landed": "accepted", "missing": "rejected"}
_STATE_KEYS = frozenset({"revision", "correlation_id", "reconciled_revision", "exported_totals", "records"})
_RECORD_KEYS = frozenset({"export_id", "sequence", "kind", "delta", "correction_of", "outcome",
                          "reconciled_revision"})
_RECONCILED_KEYS = frozenset({"task_ref", "verified", "admission_mode", "reconciled_revision", "totals",
                              "counted_threads", "coverage", "gaps", "source_versions", "model_settings"})
_COVERAGE = ("complete", "incomplete")
_TRACE_ID = re.compile(r"[0-9a-f]{32}")
_SPAN_ID = re.compile(r"[0-9a-f]{16}")
_VERSION = re.compile(r"[0-9A-Za-z.+-]{1,64}")
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}")
_MAX_ITEMS = 64
_MAX_NS = 2 ** 63 - 2


def usage_details(counts):
    """Split inclusive Codex counters into Langfuse's exclusive usage buckets."""
    return {"input": counts["input_tokens"] - counts["cached_input_tokens"] - counts["cache_write_input_tokens"],
            "input_cached_tokens": counts["cached_input_tokens"],
            "input_cache_write_tokens": counts["cache_write_input_tokens"],
            "output": counts["output_tokens"] - counts["reasoning_output_tokens"],
            "output_reasoning_tokens": counts["reasoning_output_tokens"],
            "total": counts["total_tokens"]}


def plan_export(reconciled, *, export_state, now_ns=None):
    """Return a send plan for one new record, a skip with a fixed reason, or None when nothing is new."""
    _reconciled(reconciled)
    state = _state(export_state)
    if reconciled["verified"] is not True or reconciled["admission_mode"] != "ordinary":
        return _skip("not_exportable")
    totals = reconciled["totals"]
    if any(totals[key] is None for key in COUNTERS):
        return _skip("totals_unknown")
    if not _consistent(totals):
        return _skip("usage_inconsistent")
    if state is not None and any(record["outcome"] in _OPEN for record in state["records"]):
        return _skip("uncertain_pending")
    exported = dict.fromkeys(COUNTERS, 0) if state is None else state["exported_totals"]
    delta = {key: totals[key] - exported[key] for key in COUNTERS}
    if not any(delta.values()):
        return None
    revision = reconciled["reconciled_revision"]
    if state is not None and state["reconciled_revision"] is not None and revision <= state["reconciled_revision"]:
        return _skip("stale_reconciliation")
    if any(value < 0 for value in delta.values()):
        return _skip("negative_delta")
    if not _consistent(delta):
        return _skip("usage_inconsistent")
    if now_ns is None:
        now_ns = time.time_ns()
    if type(now_ns) is not int or not 0 < now_ns <= _MAX_NS:
        raise ValueError("Invalid export time")
    metadata = _metadata(reconciled)
    if state is None:
        state = {"revision": 0, "correlation_id": _new_id(16, ()), "reconciled_revision": None,
                 "exported_totals": dict.fromkeys(COUNTERS, 0), "records": []}
    total = _accepted_total(state["records"])
    record = {"export_id": _new_id(8, {item["export_id"] for item in state["records"]}),
              "sequence": len(state["records"]) + 1, "kind": "total" if total is None else "adjustment",
              "delta": delta, "correction_of": None if total is None else total["export_id"],
              "outcome": "pending", "reconciled_revision": revision}
    state["records"].append(record)
    state["revision"] += 1
    body = _body(record, state["correlation_id"], metadata, now_ns)
    return {"action": "send", "record": deepcopy(record), "state": state, "body": body}


def record_outcome(export_state, export_id, outcome):
    """Settle one pending record with its delivery outcome; only an accepted record counts."""
    state = _state(export_state)
    _require(state is not None, "Invalid export state")
    _require(type(outcome) is str and outcome in DELIVERY_OUTCOMES, "Invalid export outcome")
    return _settle(state, export_id, "pending", outcome)


def recover_pending(export_state):
    """After a restart nothing is re-sent: every pending record becomes uncertain."""
    state = _state(export_state)
    if state is None:
        return None
    changed = [record for record in state["records"] if record["outcome"] == "pending"]
    for record in changed:
        record["outcome"] = "uncertain"
    if changed:
        state["revision"] += 1
    return state


def resolve_uncertain(export_state, export_id, resolution):
    """Apply the user's finding for an uncertain record: ``landed`` counts it, ``missing`` rejects it."""
    state = _state(export_state)
    _require(state is not None, "Invalid export state")
    _require(type(resolution) is str and resolution in _RESOLUTIONS, "Invalid export resolution")
    return _settle(state, export_id, "uncertain", _RESOLUTIONS[resolution])


def deliver(planned, *, connection):
    """POST one planned body once through ``connection``; the result names only the outcome."""
    _require(type(planned) is dict and planned.get("action") == "send" and type(planned.get("body")) is dict
             and type(planned.get("record")) is dict and planned["record"].get("outcome") == "pending",
             "Invalid export plan")
    try:
        span_id = planned["body"]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["spanId"]
    except (KeyError, IndexError, TypeError):
        span_id = None
    _require(type(span_id) is str and span_id == planned["record"].get("export_id"), "Invalid export plan")
    outcome = connection.ingest(OTLP_PATH, planned["body"])
    return {"outcome": outcome if outcome in DELIVERY_OUTCOMES else "uncertain"}


def _skip(reason):
    return {"action": "skip", "reason": reason}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _consistent(counts):
    return (all(value >= 0 for value in usage_details(counts).values())
            and counts["input_tokens"] + counts["output_tokens"] == counts["total_tokens"])


def _new_id(size, taken):
    while True:
        value = secrets.token_hex(size)
        if value.strip("0") and value not in taken:
            return value


def _accepted_total(records):
    return next((item for item in records if item["kind"] == "total" and item["outcome"] == "accepted"), None)


def _settle(state, export_id, expected, outcome):
    record = next((item for item in state["records"] if item["export_id"] == export_id), None)
    _require(type(export_id) is str and record is not None, "Unknown export record")
    _require(record["outcome"] == expected, "Export record cannot change")
    record["outcome"] = outcome
    if outcome == "accepted":
        for key in COUNTERS:
            state["exported_totals"][key] += record["delta"][key]
        state["reconciled_revision"] = record["reconciled_revision"]
    state["revision"] += 1
    return state


def _count(value, *, unknown=False):
    return (unknown and value is None) or (type(value) is int and value >= 0)


def _counters(value, *, unknown=False):
    return type(value) is dict and set(value) == set(COUNTERS) and all(
        _count(value[key], unknown=unknown) for key in COUNTERS)


def _reconciled(value):
    message = "Invalid task usage reconciliation"
    _require(type(value) is dict and set(value) == _RECONCILED_KEYS, message)
    _require(type(value["verified"]) is bool and type(value["admission_mode"]) is str
             and _count(value["reconciled_revision"]) and _counters(value["totals"], unknown=True)
             and type(value["task_ref"]) is dict and type(value["counted_threads"]) is list
             and type(value["coverage"]) is str and value["coverage"] in _COVERAGE, message)
    gaps, versions, threads = value["gaps"], value["source_versions"], value["model_settings"]
    _require(type(gaps) is list and all(type(gap) is dict and type(gap.get("code")) is str
                                        and gap["code"] in GAP_CODES for gap in gaps), message)
    _require(type(versions) is list and all(type(item) is str for item in versions), message)
    _require(type(threads) is list and all(type(item) is dict and type(item.get("model_settings")) is list
                                           for item in threads), message)
    for item in threads:
        for entry in item["model_settings"]:
            _require(type(entry) is dict and {"model", "effort"} <= set(entry)
                     and all(entry[key] is None or type(entry[key]) is str for key in ("model", "effort")), message)


def _state(value):
    if value is None:
        return None
    message = "Invalid export state"
    _require(type(value) is dict and set(value) == _STATE_KEYS, message)
    _require(_count(value["revision"]) and type(value["correlation_id"]) is str
             and _TRACE_ID.fullmatch(value["correlation_id"]) and value["correlation_id"].strip("0")
             and (value["reconciled_revision"] is None or _count(value["reconciled_revision"]))
             and _counters(value["exported_totals"]) and type(value["records"]) is list, message)
    records, seen, total, last_revision = value["records"], set(), None, None
    exported = dict.fromkeys(COUNTERS, 0)
    for index, record in enumerate(records):
        _require(type(record) is dict and set(record) == _RECORD_KEYS, message)
        export_id = record["export_id"]
        _require(type(export_id) is str and _SPAN_ID.fullmatch(export_id) and export_id.strip("0")
                 and export_id not in seen, message)
        seen.add(export_id)
        _require(type(record["sequence"]) is int and record["sequence"] == index + 1
                 and type(record["kind"]) is str and record["kind"] in _KINDS
                 and type(record["outcome"]) is str and record["outcome"] in OUTCOMES
                 and _counters(record["delta"]) and _count(record["reconciled_revision"]), message)
        # Only the newest record may still be open: planning stops while one is pending or uncertain.
        _require(record["outcome"] not in _OPEN or index == len(records) - 1, message)
        if total is None:
            _require(record["kind"] == "total" and record["correction_of"] is None, message)
        else:
            _require(record["kind"] == "adjustment" and record["correction_of"] == total["export_id"], message)
        if record["outcome"] == "accepted":
            _require(last_revision is None or record["reconciled_revision"] > last_revision, message)
            last_revision = record["reconciled_revision"]
            for key in COUNTERS:
                exported[key] += record["delta"][key]
            if total is None:
                total = record
    _require(value["exported_totals"] == exported and value["reconciled_revision"] == last_revision, message)
    return deepcopy(value)


def _metadata(reconciled):
    message = "Export metadata refused"
    versions = set(reconciled["source_versions"])
    pairs = {tuple("-" if entry[key] is None else entry[key] for key in ("model", "effort"))
             for item in reconciled["model_settings"] for entry in item["model_settings"]}
    gaps = {gap["code"] for gap in reconciled["gaps"]}
    _require(all(_VERSION.fullmatch(item) for item in versions)
             and all(label == "-" or _LABEL.fullmatch(label) for pair in pairs for label in pair)
             and all(re.fullmatch(r"[a-z_]{1,64}", code) for code in gaps)
             and max(len(versions), len(pairs), len(gaps)) <= _MAX_ITEMS, message)
    return {"coverage": reconciled["coverage"], "gap_codes": ",".join(sorted(gaps)),
            "cli_versions": ",".join(sorted(versions)),
            "configured_models": ",".join(sorted(model + "/" + effort for model, effort in pairs))}


def _body(record, correlation_id, metadata, now_ns):
    values = {"kind": record["kind"], "sequence": str(record["sequence"]),
              "correction_of": record["correction_of"] or ""} | metadata
    attributes = {"langfuse.observation.type": "generation", "langfuse.trace.name": SPAN_NAME,
                  "langfuse.observation.usage_details": json.dumps(usage_details(record["delta"]),
                                                                   separators=(",", ":"))}
    attributes |= {"langfuse.observation.metadata." + key: value for key, value in values.items()}
    span = dict(traceId=correlation_id, spanId=record["export_id"], name=SPAN_NAME, kind=1,
                startTimeUnixNano=str(now_ns), endTimeUnixNano=str(now_ns + 1),
                attributes=[dict(key=key, value=dict(stringValue=value)) for key, value in attributes.items()])
    return {"resourceSpans": [{"resource": {"attributes": [
        {"key": "service.name", "value": {"stringValue": SERVICE_NAME}}]},
        "scopeSpans": [{"scope": {"name": SCOPE_NAME}, "spans": [span]}]}]}
