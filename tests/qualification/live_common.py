"""Shared parts of the live-session modes (`live_modes.py`: launcher probes, the S3.6c drill, the OpenRouter gateway).

The cap gate every mode passes before anything starts, a call reservation that settles conservatively, the
transport wrapper that proves a zero-call step sent nothing, and the count of model requests a launcher-started
server made (it has no recorder plugins, so assistant messages are the count). Unit-tested in
tests/test_qualification_purposes.py and the per-mode test modules."""
import re

from tests.qualification.safety import BudgetExceeded, Refused

LIVE_PURPOSES = ("isolation", "drill", "gateway")
# Endpoints through which a model request can start: a prompt, a command, a shell command (whose output a model may
# read) and a summarize/compaction. A zero-call step refuses them all before anything is sent.
SENDING = re.compile(r"^/api/session/[^/]+/(?:prompt|command|shell|summarize|compact|init|revert|unrevert)(?:/|$)")


def require_cap(ledger, purpose, needed):
    """The purpose's totals, or `Refused`. A live-session purpose runs only after the user's approval opened its cap
    (`--set-cap` with `--approval`: the code cap is 0), with `needed` calls remaining and no reservation left open."""
    if purpose not in LIVE_PURPOSES:
        raise Refused("unknown live-session purpose")
    totals = ledger.summary()[purpose]
    if totals["cap"] <= 0:
        raise Refused("the %s purpose is closed: open it with --set-cap %s <cap> --reason ... --approval BY AT SCOPE "
                      "after the user's approval" % (purpose, purpose))
    if totals["open_reservations"]:
        raise Refused("the %s purpose has an open reservation; settle it first (--settle-dead)" % purpose)
    if totals["remaining"] < needed:
        raise Refused("the %s purpose has %d calls remaining and this run needs %d; a raise needs the user's approval "
                      "through --set-cap" % (purpose, totals["remaining"], needed))
    return totals


class ZeroCall(Exception):
    """A zero-call step tried an endpoint that can start a model request; nothing was sent."""


class ZeroCallHost:
    """Wraps a launcher host (`request`, `rpc`) for a step that must send no model request: every endpoint that can
    start one is refused before it reaches the server, and each refusal is counted for the proof."""

    def __init__(self, host):
        self.host, self.refused, self.requests = host, [], 0
        self.directory, self.port = getattr(host, "directory", None), getattr(host, "port", None)

    def request(self, method, path, body=None, query=None):
        if method != "GET" and SENDING.match(path.split("?", 1)[0]):
            self.refused.append(path.split("/")[-1])
            raise ZeroCall("a zero-call step may not use %s" % path.split("/")[-1])
        self.requests += 1
        return self.host.request(method, path, body=body, query=query)

    def rpc(self, method, payload):
        return self.host.rpc(method, payload)


def data(body):
    return body.get("data", body) if isinstance(body, dict) else body


def sessions(host):
    status, body = host.request("GET", "/api/session")
    listed = data(body) if status == 200 else None
    return [item for item in listed or [] if isinstance(item, dict) and isinstance(item.get("id"), str)] \
        if isinstance(listed, list) else None


def message_counts(host, session_ids=None):
    """`{sessions, user, assistant, readable}` over every listed session (or `session_ids`): each assistant message
    is one model request OpenCode transmitted. Titled sessions get no title request, and a launcher-started server
    loads no recorder, so this is the count; `readable` is False when any read failed (the count is then unknown)."""
    ids = session_ids if session_ids is not None else [item["id"] for item in sessions(host) or []]
    counts = dict(sessions=len(ids), user=0, assistant=0, readable=session_ids is not None or sessions(host) is not None)
    for sid in ids:
        status, body = host.request("GET", "/api/session/%s/message" % sid, query={"order": "asc"})
        page = data(body) if status == 200 else None
        if not isinstance(page, list):
            counts["readable"] = False
            continue
        for message in page:
            kind = message.get("type") if isinstance(message, dict) else None
            if kind in ("user", "assistant"):
                counts[kind] += 1
    return counts


def zero_call_proof(wrapped, counts):
    """The machine-checked proof that a step sent nothing: no sending endpoint was used (the wrapper refused none
    because none was tried), the counts were readable, and no session has a user or assistant message."""
    reasons = []
    if wrapped.refused:
        reasons.append("sending_endpoint_attempted")
    if not counts.get("readable"):
        reasons.append("messages_unreadable")
    if counts.get("user") or counts.get("assistant"):
        reasons.append("messages_present")
    return dict(proven=not reasons, reasons=reasons, sessions=counts.get("sessions"), assistant=counts.get("assistant"),
                user=counts.get("user"), refused_endpoints=len(wrapped.refused))


class Reservation:
    """One write-ahead reservation for a step that may send. It settles at the counted requests only when the count
    was readable; otherwise at the full reserved count (a launcher-started server has no hook guard, so an unread
    count is never taken as zero). More than reserved is recorded by the ledger and raised as BudgetExceeded."""

    def __init__(self, ledger, purpose, label, calls, run):
        self.ledger, self.calls = ledger, calls
        self.ref = ledger.reserve(purpose, label, calls, run=run)
        self.settled = None

    def settle(self, counts):
        if self.settled is not None:
            return self.settled
        readable = bool(counts and counts.get("readable"))
        actual = counts["assistant"] if readable else self.calls
        self.settled = dict(reserved=self.calls, sent=actual, basis="assistant_messages" if readable else "reserved")
        try:
            self.ledger.settle(self.ref, actual, None if readable else "count unreadable: settled at the reserved count")
        except BudgetExceeded:
            self.settled["over_reserved"] = True
            raise
        return self.settled

    def settle_unknown(self):
        return self.settle(None)
