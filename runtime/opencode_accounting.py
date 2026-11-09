"""OpenCode 2.2.6 usage accounting and event-stream resync for Smart Routing (S3.6b-1).

Pure functions over the outputs of runtime/adapters/opencode.py (normalize_message, normalize_event): no I/O, no
clock, deterministic, inputs never mutated. Usage comes from stored message and session token totals, never from
`session.usage.updated` (volatile, not replayed). Title requests create no message, so their tokens are the session
total minus its messages. Durable events carry a per-aggregate `durable.seq`; a gap, conflict or reset means the
caller re-reads session and message state, and totals are always recomputed from that state, never accumulated.

Units stay separate: token classes, USD micro-units and quota fractions are never added or compared across units.
A subscription route's cost is unknown: a provider's token-price estimate is not proof of out-of-pocket spend.
On API routes OpenCode's cost is still only an estimate from a local price catalog (0 for unpriced models), so it
is reported with source `estimate` and a zero cost for a request that used tokens is unknown.

Wiring (S3.6b-2 and the launcher): once the attempt's sessions are idle, the launcher re-reads the root session, its
spawned descendant sessions (including `subagent: true` command children, linked only by `parentID`) and all their
messages, normalizes them with the adapter and `normalize_session`, and sends `usage_from_messages(..., lineage=...)
["usage"]` in `record complete {usage}`; `lineage` lists every session it knows of under the root.
Errors are AccountingError with a fixed reason that never includes payload text.
"""

import math
from decimal import Decimal, ROUND_CEILING

from runtime.adapters import opencode

TOKEN_FIELDS = ("input", "output", "reasoning", "cache_read", "cache_write")  # usage_budget.TOKEN_FIELDS
SOURCE = "host_message_totals"  # subscription routes: host token totals, no dollar cost
ESTIMATE = "estimate"  # API routes: OpenCode's catalog-priced cost
MAX_USAGE_REQUESTS = 32  # routing.MAX_USAGE_REQUESTS: one `record` usage report
MAX_TOTAL = 2 ** 53 - 1  # usage_budget.MAX_INTEGER: the ledger's integer bound
MAX_MESSAGES = 4096
MAX_EVENTS = 4096
MAX_AGGREGATES = 1024
MAX_SESSIONS = 64  # the root and its descendant sessions in one attempt
MAX_LINEAGE = 1024  # sessions the launcher lists from session listing and lineage events
SESSION_KEYS = {"session_id", "parent_session_id", "messages", "tokens", "cost", "complete"}
LINEAGE_KEYS = {"session_id", "parent_session_id"}
MAX_RECENT = 64  # sequence numbers remembered per aggregate to tell a duplicate from a conflict
MAX_SECONDS = 10 ** 11  # an epoch-seconds reset above this looks like milliseconds
KINDS = {"assistant": "primary", "compaction": "compaction"}  # message types that carry a model request's tokens
FUNDING = ("subscription", "api")
UNKNOWN_MODEL = "unknown"
# Reasons that leave part of the session's tokens unattributed, so the title remainder and any dollar cost are unknown.
COVERAGE = ("incomplete_coverage", "message_tokens_missing", "message_in_progress", "unknown_record",
            "unexpected_tokens")
# Reasons that leave part of the attempt's spend unattributed, so every dollar cost is unknown.
COST_BLOCKING = COVERAGE + ("title_unknown", "title_cost_inconsistent", "child_session_unaccounted",
                            "tool_calls_dropped", "lineage_not_checked")
REASONS = ("invalid input", "invalid message", "invalid session", "invalid tokens", "invalid cost",
           "invalid rate limit", "invalid event", "invalid tracker state", "invalid previous", "too many messages",
           "too many events", "too many aggregates", "too many routes", "total too large",
           "conflicting duplicate message", "message in two sessions", "session not in the attempt",
           "session mismatch", "invalid lineage", "too many sessions")
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
    if record.get("kind") == "unknown" and record.get("source") == "message":
        return {"id": message_id, "type": None, "unknown": True}
    if record.get("kind") == "synthetic":
        # A command child's result delivered to its parent: no model request, never counted.
        if record.get("type") != "synthetic":
            _fail("invalid message")
        return {"id": message_id, "type": "synthetic", "unknown": False, "requested": None,
                "tokens": _tokens(record.get("tokens")), "cost": None,
                "created_ms": _count(record.get("created_ms"), "invalid message"),
                "completed_ms": _count(record.get("completed_ms"), "invalid message"), "finish": None,
                "children": (), "unlinked": 0, "dropped": 0, "has_rate_limit": False}
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
    calls = record.get("tool_calls") or []
    if type(calls) is not list or len(calls) > opencode.MAX_ITEMS or any(type(call) is not dict for call in calls):
        _fail("invalid message")
    links = [(call.get("tool"), _name(call.get("child_session_id"), opencode.SESSION, "invalid message"))
             for call in calls]
    children = tuple(sorted({child for _, child in links} - {None}))
    # A subagent call without a child session (errored, aborted or still starting) may still have spent.
    unlinked = sum(tool == "subagent" and child is None for tool, child in links)
    return {"id": message_id, "type": record["type"], "unknown": False, "requested": requested,
            "tokens": _tokens(record.get("tokens")), "cost": _cost(record.get("cost")),
            "created_ms": _count(record.get("created_ms"), "invalid message"),
            "completed_ms": _count(record.get("completed_ms"), "invalid message"), "finish": finish,
            "children": children, "unlinked": unlinked, "dropped": _count(record.get("tool_calls_dropped"), "invalid message") or 0,
            "has_rate_limit": record.get("rate_limit") is not None}


def _projection(message):
    tokens = message.get("tokens")
    return (message["type"], message.get("requested"), None if tokens is None else tuple(tokens.values()),
            message.get("cost"), message.get("created_ms"), message.get("completed_ms"), message.get("finish"),
            message.get("children"), message.get("unlinked"), message.get("dropped"))


def _terminal(message, idle_after):
    """Finished: a completion time or finish reason, or an idle message created after it. The recorded compaction
    message has neither of its own; the session's later idle message is the only sign that it ended."""
    if message["completed_ms"] is not None or message["finish"] is not None:
        return True
    return message["created_ms"] is not None and idle_after is not None and idle_after > message["created_ms"]


def _scan(records, complete):
    """One pass over a session's messages: validated, deduplicated, with every problem counted, never raised."""
    if type(records) is not list:
        _fail("invalid message")
    if len(records) > MAX_MESSAGES:
        _fail("too many messages")
    seen, scan = {}, {"by_type": {}, "duplicates": 0, "conflicts": 0, "unknown_records": 0, "missing_tokens": 0,
                      "in_progress": 0, "missing_model": 0, "unexpected_tokens": 0, "unlinked": 0, "dropped": 0, "countable": [],
                      "rate_limited": [], "children": set()}
    for record in records:
        message = _message(record)
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
        scan["children"].update(message["children"])
        scan["dropped"] += message["dropped"]
        scan["unlinked"] += message["unlinked"]
        if message["has_rate_limit"]:
            scan["rate_limited"].append(record)
        if message["type"] not in KINDS:
            if message["tokens"] is not None and any(message["tokens"].values()):
                scan["unexpected_tokens"] += 1
            continue
        if not _known(message["tokens"]):
            scan["missing_tokens"] += 1
            continue
        if message["requested"] is None:
            scan["missing_model"] += 1
        scan["countable"].append(message)
    idle_after = max((m["created_ms"] for m in seen.values() if m["type"] == "idle" and m["created_ms"] is not None),
                     default=None)
    scan["in_progress"] = sum(not _terminal(m, idle_after) for m in scan["countable"])
    scan["countable"].sort(key=lambda m: (m["created_ms"] is not None, m["created_ms"] or 0, m["id"]))
    scan["ids"] = sorted(seen)
    reasons = set()
    for reason, key in (("message_tokens_missing", "missing_tokens"), ("message_in_progress", "in_progress"),
                        ("unknown_record", "unknown_records"), ("unexpected_tokens", "unexpected_tokens"),
                        ("model_unknown", "missing_model"), ("tool_calls_dropped", "dropped"),
                        ("child_session_unaccounted", "unlinked")):
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
    (paging gaps, `complete=False`), a message's tokens are missing or it has not finished, an unknown record or
    tokens on a non-model message appear, or the session total lacks a class; `reason` names which.
    """
    _flag(complete, "invalid input")
    scan = _scan(messages, complete)
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


def _grouped(requests, by_route=True):
    """Requests summed per (kind, model, variant), or per kind alone with model `unknown`; receipts dropped."""
    groups = {}
    for request in requests:
        key = (request["kind"], request["model"], request.get("variant") or "") if by_route else (request["kind"],)
        if key in groups:
            group = groups[key]
            group["tokens"] = _add(group["tokens"], request["tokens"])
            both = (group["cost_usd_micros"], request["cost_usd_micros"])
            group["cost_usd_micros"] = None if None in both else both[0] + both[1]
        elif by_route:
            groups[key] = {name: value for name, value in request.items() if name != "receipt"}
        else:
            groups[key] = _request(request["kind"], None, request["tokens"], None)
    return [groups[key] for key in sorted(groups)]


def _api_cost(cost, tokens):
    """A reported USD cost as micro-units, or None when it is no evidence: absent, or zero (or less) for a request
    that used tokens. OpenCode computes the figure from a local price catalog and reports 0 for unpriced models."""
    if cost is None or (cost <= 0 and any(tokens.values())):
        return None
    return _micros(cost)


def _session_state(value, root=True):
    if type(value) is not dict or not {"session_id", "messages"} <= set(value) <= (
            SESSION_KEYS | {"children", "lineage"} if root else SESSION_KEYS):
        _fail("invalid session")
    _flag(value.get("complete", True), "invalid session")
    _name(value.get("parent_session_id"), opencode.SESSION, "invalid session")
    _cost(value.get("cost"))
    children = value.get("children") or []
    if type(children) is not list or len(children) >= MAX_SESSIONS:
        _fail("invalid session")
    return value, [_session_state(child, root=False)[0] for child in children]


def _part(state):
    """One session's scan, title remainder and session cost."""
    session_id = _session(state["session_id"])
    scan = _scan(state["messages"], state.get("complete", True))
    if scan["conflicts"]:
        _fail("conflicting duplicate message")
    total = _tokens(state.get("tokens"))
    title, reason, _ = _remainder(total, scan)
    return {"session_id": session_id, "parent": state.get("parent_session_id"), "scan": scan, "total": total,
            "cost": _cost(state.get("cost")), "title": title, "title_reason": reason}


def _attempt(root, children, funding, lineage):
    """Account a root session and the descendant sessions its spawns created (one attempt)."""
    if sum(len(state["messages"]) if type(state["messages"]) is list else 0 for state in [root] + children) \
            > MAX_MESSAGES:
        _fail("too many messages")
    parts = [_part(root)] + sorted((_part(child) for child in children), key=lambda part: part["session_id"])
    ids = [part["session_id"] for part in parts]
    if len(set(ids)) != len(ids):
        _fail("invalid session")
    owners = {}
    for part in parts:
        for message_id in part["scan"]["ids"]:
            if owners.setdefault(message_id, part["session_id"]) != part["session_id"]:
                _fail("message in two sessions")
    spawned = set().union(*(part["scan"]["children"] for part in parts))
    referrers = _linked(parts)
    unknown = set().union(*(part["scan"]["reasons"] for part in parts))
    if spawned - set(ids):
        unknown.add("child_session_unaccounted")
    if lineage is None:
        unknown.add("lineage_not_checked")
    elif _lineage_check(lineage, parts, referrers):
        unknown.add("child_session_unaccounted")
    messages, titles = [], []
    for part in parts:
        countable = part["scan"]["countable"]
        messages += [((m["created_ms"] is not None, m["created_ms"] or 0, m["id"]),
                      _request(KINDS[m["type"]], m["requested"], m["tokens"], _api_cost(m["cost"], m["tokens"]),
                               m["id"])) for m in countable]
        title = part["title"]
        if title is None:
            unknown |= {"title_unknown", part["title_reason"]}
            continue
        costs = [m["cost"] for m in countable]
        delta = None if part["cost"] is None or None in costs else part["cost"] - sum(costs)
        if any(title.values()):
            titles.append(_request("title", None, title, _micros(delta) if delta is not None and delta > 0 else None))
        elif funding == "api" and delta is not None and delta != 0:
            if delta < 0:
                unknown.add("title_cost_inconsistent")
            else:
                titles.append(_request("title", None, title, _micros(delta)))
    requests = [request for _, request in sorted(messages, key=lambda item: item[0])] + titles
    usd = None
    if funding == "api":
        if unknown & set(COST_BLOCKING) or any(r["cost_usd_micros"] is None for r in requests):
            unknown.add("cost_unknown")
        else:
            usd = sum(r["cost_usd_micros"] for r in requests)
            if usd > MAX_TOTAL:
                _fail("total too large")
    if usd is None:
        for request in requests:
            request["cost_usd_micros"] = None
    grouped = len(requests) > MAX_USAGE_REQUESTS
    if grouped:
        requests = _grouped(requests)
        if len(requests) > MAX_USAGE_REQUESTS:
            requests, usd = _grouped(requests, by_route=False), None
            unknown |= {"too_many_routes"} | ({"cost_unknown"} if funding == "api" else set())
    if not requests:
        unknown.add("no_requests")
    return parts, requests, usd, unknown, grouped


def _linked(parts):
    """Check the attempt's links and return each child's tool-part referrers. A child links to its parent by a
    subagent call's child session id or by its verified `parentID`; a parent outside the attempt, a parent that
    contradicts a tool-part link, or a root whose parent is in the attempt is refused."""
    ids = {part["session_id"] for part in parts}
    referrers = {}
    for part in parts:
        for child in part["scan"]["children"]:
            referrers.setdefault(child, set()).add(part["session_id"])
    links = {part["session_id"]: set(part["scan"]["children"]) for part in parts}
    if parts[0]["parent"] in ids:
        _fail("session not in the attempt")  # a root whose parent is in the attempt: a cycle through the root
    for part in parts[1:]:
        parent = part["parent"]
        if parent is None:
            continue
        if parent not in ids or (part["session_id"] in referrers and parent not in referrers[part["session_id"]]):
            _fail("session not in the attempt")
        links[parent].add(part["session_id"])
    _reachable(parts[0]["session_id"], links)
    return referrers


def _lineage(value):
    """The launcher's `[{session_id, parent_session_id}]` as a map; a session listed with two parents is refused."""
    if type(value) is not list:
        _fail("invalid lineage")
    if len(value) > MAX_LINEAGE:
        _fail("too many sessions")
    parents = {}
    for entry in value:
        if type(entry) is not dict or set(entry) != LINEAGE_KEYS:
            _fail("invalid lineage")
        session = _name(entry["session_id"], opencode.SESSION, "invalid lineage", nullable=False)
        parent = _name(entry["parent_session_id"], opencode.SESSION, "invalid lineage")
        if parents.setdefault(session, parent) != parent:
            _fail("invalid lineage")
    return parents


def _missing(parents, included, root):
    """How many listed sessions descend from an included session (so from the root) but are not included.

    Follows each listed session's recorded parent chain (iteratively, each session once). A chain that leaves the
    list ends outside the attempt; a cycle, or a root whose recorded parent descends from the attempt, is refused."""
    under = {}
    for start in parents:
        path, seen, current = [], set(), start
        while True:
            if current in included:
                found = True
                break
            if current in under:
                found = under[current]
                break
            if current not in parents or parents[current] is None:
                found = False
                break
            if current in seen:
                _fail("invalid lineage")
            seen.add(current)
            path.append(current)
            current = parents[current]
        under.update(dict.fromkeys(path, found))
    parent = parents.get(root)
    if parent is not None and (parent in included or under.get(parent)):
        _fail("invalid lineage")
    return sum(found and session not in included for session, found in under.items())


def _lineage_check(value, parts, referrers):
    """Unaccounted descendants listed in `lineage`, after checking it agrees with the included sessions' links."""
    parents = _lineage(value)
    root = parts[0]["session_id"]
    for part in parts[1:]:
        session = part["session_id"]
        if session in parents and parents[session] != part["parent"] and (
                part["parent"] is not None or parents[session] not in referrers.get(session, ())):
            _fail("invalid lineage")
    return _missing(parents, {part["session_id"] for part in parts}, root)


def _reachable(root, links):
    """Every included session must be reachable from the root through spawn links, with no link back to the root
    and no cycle (depth-first, iterative). Links to sessions that are not included are left to the caller."""
    state, stack = {root: "open"}, [(root, iter(sorted(links[root])))]
    while stack:
        session, pending = stack[-1]
        child = next(pending, None)
        if child is None:
            state[session] = "done"
            stack.pop()
        elif child in links:
            if state.get(child) == "open" or child == root:
                _fail("session not in the attempt")  # a cycle or a link back to the root
            if child not in state:
                state[child] = "open"
                stack.append((child, iter(sorted(links[child]))))
    if set(links) - set(state):
        _fail("session not in the attempt")  # not reachable from the root


def _title(part):
    title = part["title"]
    return {"state": "unknown" if title is None else "known" if any(title.values()) else "zero", "tokens": title,
            "reason": part["title_reason"]}


@_guard
def usage_from_messages(messages, session_total=None, *, session_id, funding="subscription", complete=True,
                        session_cost=None, children=None, lineage=None):
    """Account one attempt from its re-read, normalized root session and the descendant sessions it spawned.

    `children` lists descendant session states `{session_id, parent_session_id?, messages, tokens?, cost?,
    complete?}` (`normalize_session` output plus messages). Each must be reached from the root through subagent
    calls' child session ids or verified `parent_session_id` links (a `subagent: true` command child has only the
    latter); a parent outside the attempt or contradicting a tool-part link is refused. A spawned session that is not
    included leaves the attempt's cost unknown (`child_session_unaccounted`). Messages are bound to the session state
    that lists them; a message id under two sessions is refused.

    `lineage` is the launcher's `[{session_id, parent_session_id}]` for every session it knows of under the root
    (session listing and lineage events); launchers must supply it. A listed session whose recorded parent chain
    reaches an included session but that is not included adds `child_session_unaccounted`; without `lineage` the
    result carries `lineage_not_checked`, so an API route's cost is unknown rather than silently complete.

    Returns `{session_id, funding, usage, totals, title, sessions, message_ids, unknown, grouped, reported_cost,
    rate_limit}`; `usage` is the S3.6a `record` usage object or None when nothing was requested. One request per
    assistant (`primary`) or compaction message with its message id as `receipt`, in creation order, plus one `title`
    request per session with a non-zero remainder (model `unknown`). Above 32 requests they are grouped by route,
    without receipts; above 32 routes by kind, with every cost unknown (`too_many_routes`).

    Cost: `subscription` keeps every `cost_usd_micros` null (source `host_message_totals`). `api` reports OpenCode's
    figure with source `estimate` (S3.6a charges an estimate at no less than the admitted estimate): each message's
    `cost` as ceiling micro-units, the title's as the session cost minus its messages'. A zero or missing cost for a
    request that used tokens, a missing session cost for a title, or any unattributed tokens make every cost null.
    """
    root, kids = _session_state({"session_id": session_id, "messages": messages, "tokens": session_total,
                                 "cost": session_cost, "complete": complete, "children": children or [],
                                 "lineage": lineage})
    return _usage(root, kids, funding)


def _usage(root, children, funding):
    if funding not in FUNDING:
        _fail("invalid input")
    parts, requests, usd, unknown, grouped = _attempt(root, children, funding, root.get("lineage"))
    counted, messages_total, sessions = dict(ZERO), dict(ZERO), []
    for part in parts:
        part_counted = part["scan"]["messages_total"] if part["title"] is None else \
            _add(part["scan"]["messages_total"], part["title"])
        counted, messages_total = _add(counted, part_counted), _add(messages_total, part["scan"]["messages_total"])
        sessions.append({"session_id": part["session_id"], "counted": part_counted, "title": _title(part)})
    totals = [part["total"] for part in parts]
    session_total = None
    if all(_known(total) for total in totals):
        session_total = dict(ZERO)
        for total in totals:
            session_total = _add(session_total, total)
    countable = [m for part in parts for m in part["scan"]["countable"]]
    reported = [m["cost"] for m in countable if m["cost"] is not None]
    rate_limited = [record for part in parts for record in part["scan"]["rate_limited"]]
    latest = max(rate_limited, key=lambda r: (_time(r), r["message_id"]), default=None)
    return {"session_id": parts[0]["session_id"], "funding": funding,
            "usage": {"source": ESTIMATE if funding == "api" else SOURCE, "requests": requests} if requests else None,
            "totals": {"counted": counted, "messages": messages_total, "session": session_total, "usd_micros": usd,
                       "usd_basis": "estimate" if usd is not None else None},
            "title": _title(parts[0]), "sessions": sessions,
            "message_ids": sorted(m for part in parts for m in part["scan"]["ids"]), "unknown": sorted(unknown),
            "grouped": grouped,
            "reported_cost": {"usd_micros": _micros(sum(reported)), "messages": len(reported),
                              "basis": "provider_estimate"} if reported else None,
            "rate_limit": None if latest is None else rate_limit_observation(latest)}


@_guard
def normalize_session(session):
    """`{session_id, parent_session_id, tokens, cost}` from a `session.get` record, its token total and cost
    validated by the adapter's own message normalizer (the adapter has no session normalizer)."""
    if type(session) is not dict:
        _fail("invalid session")
    try:
        normalized = opencode.normalize_message({"type": "assistant", "tokens": session.get("tokens"),
                                                 "cost": session.get("cost")})
    except opencode.AdapterError:
        _fail("invalid session")
    return {"session_id": _session(session.get("id")),
            "parent_session_id": _name(session.get("parentID"), opencode.SESSION, "invalid session"),
            "tokens": normalized["tokens"], "cost": normalized["cost"]}


@_guard
def consistency_report(session_state):
    """Bounded diagnostics for a re-read session (`{session_id, messages, tokens?, cost?, complete?, children?,
    lineage?}`): counts of message types, duplicates, conflicts, unknown records and missing fields, spawned,
    included and unaccounted child sessions (`lineage_unaccounted` is None without `lineage`), and whether the title
    remainder is known. Inconsistencies are counted, not raised; ids, counts and field names only, never text."""
    state, children = _session_state(session_state)
    session_id, complete = _session(state["session_id"]), state.get("complete", True)
    scan = _scan(state["messages"], complete)
    title, reason, negative = _remainder(_tokens(state.get("tokens")), scan)
    if scan["conflicts"]:
        title, reason = None, reason or "inconsistent_messages"
    included = {_session(child["session_id"]) for child in children}
    spawned = set(scan["children"])
    for child in children:
        spawned |= _scan(child["messages"], True)["children"]
    lineage = state.get("lineage")
    unlisted = None if lineage is None else _missing(_lineage(lineage), included | {session_id}, session_id)
    return {"session_id": session_id, "messages": len(state["messages"]), "by_type": dict(sorted(scan["by_type"].items())),
            "countable": len(scan["countable"]), "duplicates": scan["duplicates"],
            "conflicting_duplicates": scan["conflicts"], "missing_tokens": scan["missing_tokens"],
            "in_progress": scan["in_progress"], "unknown_records": scan["unknown_records"],
            "missing_model": scan["missing_model"], "complete": complete,
            "title": "unknown" if title is None else "known" if any(title.values()) else "zero",
            "title_reason": reason, "negative_classes": negative, "rate_limit_messages": len(scan["rate_limited"]),
            "children_referenced": len(spawned), "children_included": len(included),
            "children_unaccounted": len(spawned - included), "spawns_without_session": scan["unlinked"],
            "tool_calls_dropped": scan["dropped"], "lineage_unaccounted": unlisted}


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
    """Resync after an event gap or at attempt end: recompute the attempt's accounting from freshly re-read state
    (`{session_id, messages, tokens?, cost?, complete?, children?, lineage?}`, as for `usage_from_messages`).

    `previous` is the last `usage_from_messages` result for the same root session (or None). Returns `{accounting,
    changes, tracker}`; `changes` = `{tokens_delta, regressed, messages_added, messages_missing, usd_micros_delta}`
    against `previous`. Totals are recomputed from state, never accumulated from events, so repeating a resync
    changes nothing. A negative delta (`regressed`: a message deleted or reverted) is reported, never charged back.
    When `tracker` (a `track_events` state) is given, every included session is re-baselined in the returned copy.
    """
    state, children = _session_state(session_state)
    session_id = _session(state["session_id"])
    before, before_ids, before_usd = _previous(previous, session_id)
    result = _usage(state, children, funding)
    after = result["totals"]["counted"]
    delta = {field: after[field] - before[field] for field in TOKEN_FIELDS}
    now_ids, old_ids = set(result["message_ids"]), set(before_ids)
    usd = result["totals"]["usd_micros"]
    if tracker is not None:
        for session in result["sessions"]:
            tracker = mark_resynced(tracker, session["session_id"])
    return {"accounting": result,
            "changes": {"tokens_delta": delta, "regressed": [field for field in TOKEN_FIELDS if delta[field] < 0],
                        "messages_added": len(now_ids - old_ids), "messages_missing": len(old_ids - now_ids),
                        "usd_micros_delta": None if usd is None or (previous is not None and before_usd is None)
                        else usd - (before_usd or 0)},
            "tracker": tracker}


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


def _time(record):
    """The record's observation time: its completion time when it has one, else its creation time."""
    completed = record.get("completed_ms")
    return record.get("created_ms") if completed is None else completed


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
    observed = _count(_time(record), "invalid rate limit")
    return {"kind": "rate_limit_observation", "source": "opencode.native.rateLimit", "units": dict(RATE_UNITS),
            "observed_ms": observed, "status": _label(limit.get("status")),
            "rate_limit_type": _label(limit.get("rate_limit_type")), "resets_at_s": _reset(limit.get("resets_at_s")),
            "is_using_overage": overage, "windows": windows,
            "unknown_windows": [name for name, window in windows.items() if window["utilization_fraction"] is None]}
