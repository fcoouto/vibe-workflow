"""OpenCode 2.2.6 usage accounting and event-stream resync for Smart Routing (S3.6b-1).

Pure functions over the outputs of runtime/adapters/opencode.py (normalize_message, normalize_event): no I/O, no
clock, deterministic, inputs never mutated. Usage comes from stored message and session token totals, never from
`session.usage.updated` (volatile, not replayed). Title requests create no message, so their tokens are the session
total minus its messages. Durable events carry a per-aggregate `durable.seq`; a gap, conflict or reset means the
caller re-reads session and message state, and totals are always recomputed from that state, never accumulated.

Units stay separate: token classes, USD micro-units and quota fractions are never added or compared across units.
A subscription route's cost is unknown: a provider's token-price estimate is not proof of out-of-pocket spend.

Wiring (S3.6b-2 and the launcher): at attempt end the launcher re-reads the attempt's session and its messages,
normalizes them with the adapter and sends `usage_from_messages(...)["usage"]` in `record complete {usage}`.
Errors are AccountingError with a fixed reason that never includes payload text.
"""

import math
from decimal import Decimal, ROUND_CEILING

from runtime.adapters import opencode

TOKEN_FIELDS = ("input", "output", "reasoning", "cache_read", "cache_write")  # usage_budget.TOKEN_FIELDS
SOURCE = "host_message_totals"
MAX_USAGE_REQUESTS = 32  # routing.MAX_USAGE_REQUESTS: one `record` usage report
MAX_TOTAL = 2 ** 53 - 1  # usage_budget.MAX_INTEGER: the ledger's integer bound
MAX_MESSAGES = 4096
MAX_EVENTS = 4096
MAX_AGGREGATES = 1024
MAX_RECENT = 64  # sequence numbers remembered per aggregate to tell a duplicate from a conflict
MAX_SECONDS = 10 ** 11  # an epoch-seconds reset above this looks like milliseconds
KINDS = {"assistant": "primary", "compaction": "compaction"}  # message types that carry a model request's tokens
FUNDING = ("subscription", "api")
UNKNOWN_MODEL = "unknown"
# Reasons that leave part of the session's tokens unattributed, so the title remainder and any dollar cost are unknown.
COVERAGE = ("incomplete_coverage", "message_tokens_missing", "message_in_progress", "unknown_record",
            "unexpected_tokens")
REASONS = ("invalid input", "invalid message", "invalid session", "invalid tokens", "invalid cost",
           "invalid rate limit", "invalid event", "invalid tracker state", "invalid previous", "too many messages",
           "too many events", "too many aggregates", "too many routes", "total too large",
           "conflicting duplicate message", "message from another session", "session mismatch")
RATE_UNITS = {"utilization": "fraction_0_1", "resets_at": "epoch_seconds", "observed_at": "epoch_ms"}
ZERO = dict.fromkeys(TOKEN_FIELDS, 0)


class AccountingError(ValueError):
    """Invalid accounting input. The message is one of REASONS and never includes payload content."""


def _guard(function):
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except AccountingError as error:
            raise AccountingError(str(error)) from None
        except Exception:
            raise AccountingError("invalid input") from None
    guarded.__name__, guarded.__doc__ = function.__name__, function.__doc__
    return guarded


def _fail(reason):
    raise AccountingError(reason)


def _count(value, reason, nullable=True):
    if value is None and nullable:
        return None
    if type(value) is not int or not 0 <= value <= opencode.MAX_COUNT:
        _fail(reason)
    return value


def _name(value, pattern, reason, nullable=True):
    if value is None and nullable:
        return None
    if type(value) is not str or not pattern.fullmatch(value):
        _fail(reason)
    return value


def _cost(value):
    """A provider-reported USD amount (a JSON number) as exact Decimal, or None."""
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= opencode.MAX_COUNT:
        _fail("invalid cost")
    return Decimal(repr(value))


def _micros(amount):
    """USD as integer micro-units, rounded up so a reported cost is never under-charged."""
    micros = int((amount * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
    if micros > MAX_TOTAL:
        _fail("total too large")
    return micros


def _tokens(value, reason="invalid tokens"):
    """The adapter's normalized token classes, each an integer or None (unknown); None when absent."""
    if value is None:
        return None
    if type(value) is not dict or set(value) != set(TOKEN_FIELDS):
        _fail(reason)
    return {field: _count(value[field], reason) for field in TOKEN_FIELDS}


def _known(tokens):
    return tokens is not None and all(amount is not None for amount in tokens.values())


def _add(left, right):
    total = {field: left[field] + right[field] for field in TOKEN_FIELDS}
    if any(amount > MAX_TOTAL for amount in total.values()):
        _fail("total too large")
    return total


def _message(record):
    """Validate the fields accounting reads from one normalized message; unknown keys are ignored."""
    if type(record) is not dict or record.get("schema") != opencode.SCHEMA or record.get("client") != opencode.CLIENT:
        _fail("invalid message")
    message_id = _name(record.get("message_id"), opencode.MESSAGE, "invalid message", nullable=False)
    session_id = _name(record.get("session_id"), opencode.SESSION, "invalid message")
    if record.get("kind") == "unknown" and record.get("source") == "message":
        return {"id": message_id, "session_id": session_id, "type": None, "unknown": True}
    if record.get("kind") != "message" or record.get("type") not in opencode.MESSAGE_TYPES:
        _fail("invalid message")
    requested = record.get("requested")
    if requested is not None:
        if type(requested) is not dict:
            _fail("invalid message")
        requested = (_name(requested.get("model_alias"), opencode.NAME, "invalid message", nullable=False),
                     _name(requested.get("variant"), opencode.NAME, "invalid message"))
    finish = record.get("finish")
    if finish is not None and (type(finish) is not str or len(finish) > opencode.MAX_TEXT):
        _fail("invalid message")
    return {"id": message_id, "session_id": session_id, "type": record["type"], "unknown": False,
            "requested": requested, "tokens": _tokens(record.get("tokens")), "cost": _cost(record.get("cost")),
            "created_ms": _count(record.get("created_ms"), "invalid message"),
            "completed_ms": _count(record.get("completed_ms"), "invalid message"), "finish": finish,
            "has_rate_limit": record.get("rate_limit") is not None}


def _projection(message):
    tokens = message.get("tokens")
    return (message["type"], message.get("requested"), None if tokens is None else tuple(tokens.values()),
            message.get("cost"), message.get("created_ms"), message.get("completed_ms"), message.get("finish"),
            message.get("session_id"))


def _scan(records, session_id, complete):
    """One pass over a session's messages: validated, deduplicated, with every problem counted, never raised."""
    if type(records) is not list:
        _fail("invalid message")
    if len(records) > MAX_MESSAGES:
        _fail("too many messages")
    seen, scan = {}, {"by_type": {}, "duplicates": 0, "conflicts": 0, "foreign": 0, "unknown_records": 0,
                      "missing_tokens": 0, "in_progress": 0, "missing_model": 0, "unexpected_tokens": 0,
                      "countable": [], "rate_limited": []}
    for record in records:
        message = _message(record)
        if None not in (message["session_id"], session_id) and message["session_id"] != session_id:
            scan["foreign"] += 1
            continue
        if message["id"] in seen:
            if _projection(seen[message["id"]]) == _projection(message):
                scan["duplicates"] += 1
            else:
                scan["conflicts"] += 1
            continue
        seen[message["id"]] = message
        if message["unknown"]:
            scan["unknown_records"] += 1
            continue
        scan["by_type"][message["type"]] = scan["by_type"].get(message["type"], 0) + 1
        if message["has_rate_limit"]:
            scan["rate_limited"].append(record)
        if message["type"] not in KINDS:
            if message["tokens"] is not None and any(message["tokens"].values()):
                scan["unexpected_tokens"] += 1
            continue
        if not _known(message["tokens"]):
            scan["missing_tokens"] += 1
            continue
        if message["type"] == "assistant" and message["completed_ms"] is None and message["finish"] is None:
            scan["in_progress"] += 1
        if message["requested"] is None:
            scan["missing_model"] += 1
        scan["countable"].append(message)
    scan["countable"].sort(key=lambda m: (m["created_ms"] is not None, m["created_ms"] or 0, m["id"]))
    scan["ids"] = sorted(seen)
    reasons = set()
    for reason, key in (("message_tokens_missing", "missing_tokens"), ("message_in_progress", "in_progress"),
                        ("unknown_record", "unknown_records"), ("unexpected_tokens", "unexpected_tokens"),
                        ("model_unknown", "missing_model")):
        if scan[key]:
            reasons.add(reason)
    if not complete:
        reasons.add("incomplete_coverage")
    scan["reasons"] = reasons
    total = dict(ZERO)
    for message in scan["countable"]:
        total = _add(total, message["tokens"])
    scan["messages_total"] = total
    return scan


def _remainder(session_total, scan):
    """Title tokens by subtraction, as (tokens, reason, negative classes)."""
    blocking = sorted(scan["reasons"] & set(COVERAGE), key=COVERAGE.index)
    if blocking:
        return None, blocking[0], []
    if not _known(session_total):
        return None, "session_tokens_missing", []
    remainder = {field: session_total[field] - scan["messages_total"][field] for field in TOKEN_FIELDS}
    negative = [field for field in TOKEN_FIELDS if remainder[field] < 0]
    return (None, "negative_remainder", negative) if negative else (remainder, None, [])


def _session(session_id):
    return _name(session_id, opencode.SESSION, "invalid session", nullable=False)


def _flag(value, reason):
    if type(value) is not bool:
        _fail(reason)
    return value


@_guard
def title_tokens(session_total, messages, complete=True):
    """Session total minus the sum of its messages, per token class: `{tokens, reason}`.

    `tokens` is None (unknown) when any class would go negative, the message list is not known to be complete
    (paging gaps, `complete=False`), a message's tokens are missing or still growing, or the session total lacks a
    class; `reason` names which. Messages from one session only; `session_id` binding is checked by the callers.
    """
    _flag(complete, "invalid input")
    scan = _scan(messages, None, complete)
    if scan["conflicts"]:
        _fail("conflicting duplicate message")
    tokens, reason, _ = _remainder(_tokens(session_total), scan)
    return {"tokens": tokens, "reason": reason}


def _request(kind, model, tokens, cost, receipt=None):
    request = {"kind": kind, "model": model[0] if model else UNKNOWN_MODEL}
    if model and model[1] is not None:
        request["variant"] = model[1]
    request.update(tokens=dict(tokens), cost_usd_micros=cost)
    if receipt is not None:
        request["receipt"] = receipt
    return request


def _grouped(requests):
    groups = {}
    for request in requests:
        key = (request["kind"], request["model"], request.get("variant") or "")
        if key in groups:
            group = groups[key]
            group["tokens"] = _add(group["tokens"], request["tokens"])
            both = (group["cost_usd_micros"], request["cost_usd_micros"])
            group["cost_usd_micros"] = None if None in both else both[0] + both[1]
        else:
            groups[key] = {name: value for name, value in request.items() if name != "receipt"}
    return [groups[key] for key in sorted(groups)]


@_guard
def usage_from_messages(messages, session_total=None, *, session_id, funding="subscription", complete=True,
                        session_cost=None):
    """Account one session (one attempt) from its re-read, normalized messages and its session token total.

    Returns `{session_id, funding, usage, totals, title, message_ids, unknown, grouped, reported_cost, rate_limit}`;
    `usage` is the S3.6a `record` usage object (`{source: host_message_totals, requests}`) or None when nothing was
    requested. One request per assistant (`primary`) or compaction message, with its message id as `receipt`, plus one
    `title` request for a non-zero session remainder (model `unknown`: no message records it). Above 32 requests they
    are grouped by kind, model and variant, without receipts. Child (subagent) sessions are accounted separately.
    Cost: `subscription` keeps every `cost_usd_micros` null; `api` passes each message's reported `cost` through as
    ceiling micro-units and nulls every cost when any is missing or any tokens are unattributed. A message with a
    `session_id` other than `session_id` is refused.
    """
    session_id = _session(session_id)
    if funding not in FUNDING:
        _fail("invalid input")
    _flag(complete, "invalid input")
    session_total, session_cost = _tokens(session_total), _cost(session_cost)
    scan = _scan(messages, session_id, complete)
    if scan["foreign"]:
        _fail("message from another session")
    if scan["conflicts"]:
        _fail("conflicting duplicate message")
    title, title_reason, _ = _remainder(session_total, scan)
    unknown = set(scan["reasons"])
    if title is None:
        unknown |= {"title_unknown", title_reason}
    requests = [_request(KINDS[m["type"]], m["requested"], m["tokens"],
                         None if m["cost"] is None else _micros(m["cost"]), m["id"]) for m in scan["countable"]]
    counted = scan["messages_total"]
    if title is not None:
        counted = _add(counted, title)
        message_cost = [m["cost"] for m in scan["countable"]]
        title_cost = None if session_cost is None or None in message_cost else session_cost - sum(message_cost)
        if any(title.values()) or (funding == "api" and title_cost is not None and title_cost != 0):
            requests.append(_request("title", None, title, None if title_cost is None or title_cost < 0
                                     else _micros(title_cost)))
    reported = [m["cost"] for m in scan["countable"] if m["cost"] is not None]
    usd = None
    if funding == "api":
        if unknown & ({"title_unknown"} | set(COVERAGE)) or any(r["cost_usd_micros"] is None for r in requests):
            unknown.add("cost_unknown")
        else:
            usd = sum(r["cost_usd_micros"] for r in requests)
    if usd is None:
        for request in requests:
            request["cost_usd_micros"] = None
    grouped = len(requests) > MAX_USAGE_REQUESTS
    if grouped:
        requests = _grouped(requests)
        if len(requests) > MAX_USAGE_REQUESTS:
            _fail("too many routes")
    if not requests:
        unknown.add("no_requests")
    if usd is not None and usd > MAX_TOTAL:
        _fail("total too large")
    latest = max(scan["rate_limited"], key=lambda r: (r.get("completed_ms") or r.get("created_ms") or 0,
                                                      r["message_id"]), default=None)
    return {"session_id": session_id, "funding": funding,
            "usage": {"source": SOURCE, "requests": requests} if requests else None,
            "totals": {"counted": counted, "messages": scan["messages_total"], "session": session_total,
                       "usd_micros": usd},
            "title": {"state": "unknown" if title is None else "known" if any(title.values()) else "zero",
                      "tokens": title, "reason": title_reason},
            "message_ids": scan["ids"], "unknown": sorted(unknown), "grouped": grouped,
            "reported_cost": {"usd_micros": _micros(sum(reported)), "messages": len(reported),
                              "basis": "provider_estimate"} if reported else None,
            "rate_limit": None if latest is None else rate_limit_observation(latest)}


def _session_state(value):
    if type(value) is not dict or not {"session_id", "messages"} <= set(value) <= {
            "session_id", "messages", "tokens", "cost", "complete"}:
        _fail("invalid session")
    complete = value.get("complete", True)
    _flag(complete, "invalid session")
    _cost(value.get("cost"))
    return value


@_guard
def consistency_report(session_state):
    """Bounded diagnostics for a re-read session (`{session_id, messages, tokens?, cost?, complete?}`): counts of
    message types, duplicates, conflicts, foreign or unknown records and missing fields, and whether the title
    remainder is known. Inconsistencies are counted, not raised; ids, counts and field names only, never text."""
    state = _session_state(session_state)
    session_id, complete = _session(state["session_id"]), state.get("complete", True)
    scan = _scan(state["messages"], session_id, complete)
    title, reason, negative = _remainder(_tokens(state.get("tokens")), scan)
    if scan["conflicts"] or scan["foreign"]:
        title, reason = None, reason or "inconsistent_messages"
    return {"session_id": session_id, "messages": len(state["messages"]), "by_type": dict(sorted(scan["by_type"].items())),
            "countable": len(scan["countable"]), "duplicates": scan["duplicates"],
            "conflicting_duplicates": scan["conflicts"], "foreign_session": scan["foreign"],
            "missing_tokens": scan["missing_tokens"], "in_progress": scan["in_progress"],
            "unknown_records": scan["unknown_records"], "missing_model": scan["missing_model"], "complete": complete,
            "title": "unknown" if title is None else "known" if any(title.values()) else "zero",
            "title_reason": reason, "negative_classes": negative, "rate_limit_messages": len(scan["rate_limited"])}


def _previous(previous, session_id):
    if previous is None:
        return dict(ZERO), [], None
    if type(previous) is not dict or type(previous.get("totals")) is not dict:
        _fail("invalid previous")
    if _name(previous.get("session_id"), opencode.SESSION, "invalid previous", nullable=False) != session_id:
        _fail("session mismatch")
    counted = _tokens(previous["totals"].get("counted"), "invalid previous")
    ids, usd = previous.get("message_ids"), previous["totals"].get("usd_micros")
    if not _known(counted) or type(ids) is not list or len(ids) > MAX_MESSAGES or \
            (usd is not None and (type(usd) is not int or not 0 <= usd <= MAX_TOTAL)):
        _fail("invalid previous")
    for message_id in ids:
        _name(message_id, opencode.MESSAGE, "invalid previous", nullable=False)
    return counted, ids, usd


@_guard
def reconcile_with_state(previous, session_state, *, funding="subscription", tracker=None):
    """Resync after an event gap or at attempt end: recompute the session's accounting from freshly re-read state.

    `previous` is the last `usage_from_messages` result for the same session (or None). Returns `{accounting,
    changes, tracker}`; `changes` = `{tokens_delta, regressed, messages_added, messages_missing, usd_micros_delta}`
    against `previous`. Totals are recomputed from state, never accumulated from events, so repeating a resync
    changes nothing. A negative delta (`regressed`: a message deleted or reverted) is reported, never charged back.
    When `tracker` (a `track_events` state) is given, the session's aggregate is re-baselined in the returned copy.
    """
    state = _session_state(session_state)
    session_id = _session(state["session_id"])
    before, before_ids, before_usd = _previous(previous, session_id)
    result = usage_from_messages(state["messages"], state.get("tokens"), session_id=session_id, funding=funding,
                                 complete=state.get("complete", True), session_cost=state.get("cost"))
    after = result["totals"]["counted"]
    delta = {field: after[field] - before[field] for field in TOKEN_FIELDS}
    now_ids, old_ids = set(result["message_ids"]), set(before_ids)
    usd = result["totals"]["usd_micros"]
    return {"accounting": result,
            "changes": {"tokens_delta": delta, "regressed": [field for field in TOKEN_FIELDS if delta[field] < 0],
                        "messages_added": len(now_ids - old_ids), "messages_missing": len(old_ids - now_ids),
                        "usd_micros_delta": None if usd is None or (previous is not None and before_usd is None)
                        else usd - (before_usd or 0)},
            "tracker": None if tracker is None else mark_resynced(tracker, session_id)}


def _aggregate(value):
    if type(value) is not dict or set(value) != {"last_seq", "version", "recent", "resync"}:
        _fail("invalid tracker state")
    recent, resync = value["recent"], value["resync"]
    if type(recent) is not list or len(recent) > MAX_RECENT:
        _fail("invalid tracker state")
    pairs = []
    for pair in recent:
        if type(pair) is not list or len(pair) != 2:
            _fail("invalid tracker state")
        pairs.append([_count(pair[0], "invalid tracker state", False),
                      _name(pair[1], opencode.EVENT, "invalid tracker state", nullable=False)])
    if resync is not None:
        if type(resync) is not dict or set(resync) != {"last_good_seq", "reason"} or \
                resync["reason"] not in SEQ_REASONS:
            _fail("invalid tracker state")
        resync = {"last_good_seq": _count(resync["last_good_seq"], "invalid tracker state"),
                  "reason": resync["reason"]}
    return {"last_seq": _count(value["last_seq"], "invalid tracker state"),
            "version": _count(value["version"], "invalid tracker state"), "recent": pairs, "resync": resync}


def _tracker(state):
    """A validated deep copy, so the caller's state is never mutated."""
    if state is None:
        return {}
    if type(state) is not dict or set(state) != {"aggregates"} or type(state["aggregates"]) is not dict or \
            len(state["aggregates"]) > MAX_AGGREGATES:
        _fail("invalid tracker state")
    return {_name(key, opencode.SESSION, "invalid tracker state", nullable=False): _aggregate(value)
            for key, value in state["aggregates"].items()}


SEQ_REASONS = ("gap", "out_of_order", "seq_conflict", "reset", "version_changed", "joined_late")


def _durable(record):
    if type(record) is not dict or record.get("schema") != opencode.SCHEMA or record.get("client") != opencode.CLIENT \
            or record.get("kind") not in ("event", "unknown"):
        _fail("invalid event")
    seq, aggregate = record.get("durable_seq"), record.get("durable_aggregate_id")
    if seq is None and aggregate is None:
        return None
    return (_name(aggregate, opencode.SESSION, "invalid event", nullable=False), _count(seq, "invalid event", False),
            _name(record.get("event_id"), opencode.EVENT, "invalid event", nullable=False),
            _count(record.get("durable_version"), "invalid event"))


def _observe(entry, seq, event_id, version):
    """The problem one durable event reveals for its aggregate (None when in order), updating `entry`."""
    known = dict(entry["recent"])
    last = entry["last_seq"]
    problem = None
    if seq in known:
        if known[seq] == event_id:
            return "duplicate"
        problem = "reset" if seq == 0 else "seq_conflict"
    elif last is not None and seq <= last:
        problem = "reset" if seq == 0 else "out_of_order"
    elif last is not None and seq != last + 1:
        problem = "gap"
    if version is not None and entry["version"] is not None and version != entry["version"]:
        problem = problem or "version_changed"
    if problem and entry["resync"] is None:
        entry["resync"] = {"last_good_seq": last, "reason": problem}
    entry["last_seq"] = seq if last is None else max(last, seq)
    entry["version"] = version if version is not None else entry["version"]
    entry["recent"] = sorted({**known, seq: event_id}.items())[-MAX_RECENT:]
    entry["recent"] = [list(pair) for pair in entry["recent"]]
    return problem


@_guard
def track_events(events, state=None):
    """Check `durable.seq` continuity per aggregate over normalized `/api/event` records.

    Returns `{state, resync_needed, counts}`. `resync_needed` lists every aggregate whose first problem since its
    last resync was a `gap`, `out_of_order`, `seq_conflict` (same seq, another event), `reset` (seq 0 again),
    `version_changed` or `joined_late` (first seen after seq 0), with the last in-order seq. An exact duplicate is
    only counted. Volatile events (no `durable`, such as `session.usage.updated`) are ignored. The caller re-reads
    session and message state (`reconcile_with_state`) and then calls `mark_resynced`; the input state is not mutated.
    """
    aggregates = _tracker(state)
    if type(events) is not list:
        _fail("invalid event")
    if len(events) > MAX_EVENTS:
        _fail("too many events")
    counts = {"durable": 0, "volatile_ignored": 0, "duplicates": 0}
    for record in events:
        durable = _durable(record)
        if durable is None:
            counts["volatile_ignored"] += 1
            continue
        aggregate, seq, event_id, version = durable
        counts["durable"] += 1
        if aggregate not in aggregates:
            if len(aggregates) >= MAX_AGGREGATES:
                _fail("too many aggregates")
            aggregates[aggregate] = {"last_seq": None, "version": None, "recent": [],
                                     "resync": None if seq == 0 else {"last_good_seq": None, "reason": "joined_late"}}
        if _observe(aggregates[aggregate], seq, event_id, version) == "duplicate":
            counts["duplicates"] += 1
    resync = [{"aggregate_id": key, **aggregates[key]["resync"]} for key in sorted(aggregates)
              if aggregates[key]["resync"] is not None]
    return {"state": {"aggregates": dict(sorted(aggregates.items()))}, "resync_needed": resync, "counts": counts}


@_guard
def mark_resynced(state, aggregate_id):
    """A copy of `state` with `aggregate_id` cleared after a re-read; its next durable event becomes the baseline."""
    aggregates = _tracker(state)
    aggregate_id = _name(aggregate_id, opencode.SESSION, "invalid tracker state", nullable=False)
    if aggregate_id not in aggregates and len(aggregates) >= MAX_AGGREGATES:
        _fail("too many aggregates")
    aggregates[aggregate_id] = {"last_seq": None, "version": None, "recent": [], "resync": None}
    return {"aggregates": dict(sorted(aggregates.items()))}


def _utilization(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        _fail("invalid rate limit")  # percent-looking (>1), negative, NaN or bool
    return value


def _reset(value):
    value = _count(value, "invalid rate limit")
    if value is not None and value >= MAX_SECONDS:
        _fail("invalid rate limit")  # milliseconds, not epoch seconds
    return value


def _label(value):
    return _name(value, opencode.NAME, "invalid rate limit")


@_guard
def rate_limit_observation(record):
    """Claude Agent `native.rateLimit` telemetry from a normalized message or event, as an observation only.

    Units are explicit (utilization 0-1 fractions, resets in epoch seconds, observation time in epoch ms); windows
    whose utilization the adapter could not read (such as a percentage) are listed in `unknown_windows`. None when
    the record carries no rate limit. Window headroom comes from telemetry, never from local token arithmetic.
    """
    if type(record) is not dict or record.get("kind") not in ("message", "event"):
        _fail("invalid rate limit")
    limit = record.get("rate_limit")
    if limit is None:
        return None
    if type(limit) is not dict or type(limit.get("windows")) is not dict or len(limit["windows"]) > 8:
        _fail("invalid rate limit")
    windows = {}
    for name, window in sorted(limit["windows"].items()):
        if type(window) is not dict:
            _fail("invalid rate limit")
        windows[_label(name)] = {"utilization_fraction": _utilization(window.get("utilization_fraction")),
                                 "resets_at_s": _reset(window.get("resets_at_s")), "status": _label(window.get("status"))}
    overage = limit.get("is_using_overage")
    if overage is not None and type(overage) is not bool:
        _fail("invalid rate limit")
    observed = _count(record.get("completed_ms") or record.get("created_ms"), "invalid rate limit")
    return {"kind": "rate_limit_observation", "source": "opencode.native.rateLimit", "units": dict(RATE_UNITS),
            "observed_ms": observed, "status": _label(limit.get("status")),
            "rate_limit_type": _label(limit.get("rate_limit_type")), "resets_at_s": _reset(limit.get("resets_at_s")),
            "is_using_overage": overage, "windows": windows,
            "unknown_windows": [name for name, window in windows.items() if window["utilization_fraction"] is None]}
