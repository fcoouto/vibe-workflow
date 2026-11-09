"""Shared recorded-probe fixtures and helpers for the OpenCode accounting tests (no tests here)."""
import json
from pathlib import Path
import re

from runtime import opencode_accounting as accounting
from runtime.adapters import opencode
from runtime.opencode_accounting import AccountingError
from tests import fixture_routing_accounting as ledger_tests


M = ledger_tests.M
ROOT = Path(__file__).resolve().parents[1]
PROBES = ROOT / "tests" / "fixtures" / "smart-routing" / "probes"
MODULE = ROOT / "runtime" / "opencode_accounting.py"
FAMILIES = {"claude-agent/haiku": "claude-haiku"}
PLACEHOLDER = re.compile(r"<(ses|msg|toolu|evt)([_#])(\d+)>")
MARKER = "HOSTILE-MARKER-do-not-echo"
FIELDS = ("input", "output", "reasoning", "cache_read", "cache_write")


def desanitize(value):
    """Map sanitizer placeholders (`<ses_5>`) to valid-looking IDs, as tests/test_opencode_adapter.py does."""
    if isinstance(value, dict):
        return {key: desanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [desanitize(item) for item in value]
    if isinstance(value, str):
        return PLACEHOLDER.sub(lambda m: f"{m[1]}_{'fixture' if m[2] == '_' else 'hook'}{m[3]}", value)
    return value


def load(name):
    return desanitize(json.loads((PROBES / name).read_text(encoding="utf-8")))


LINEAGE = load("p0-8-lineage-aux.json")
EXCERPT = load("p0-hook-event-excerpt.json")
SESSIONS = {session["id"]: session for session in LINEAGE["sessions"]}


def normalized(raw_messages):
    return [opencode.normalize_message(message, families=FAMILIES) for message in raw_messages]


def total_of(raw_tokens):
    """A session total in the adapter's normalized token shape, through its public message normalizer."""
    return opencode.normalize_message({"type": "assistant", "tokens": raw_tokens})["tokens"]


def tokens(*values):
    return dict(zip(FIELDS, values))


# Session C (`ses_fixture4`, generated title "Reply with exact text"): a compaction and an assistant message.
C_ID = "ses_fixture4"


C_MESSAGES = normalized(LINEAGE["aux_session_C"]["messages"])
C_TOTAL = total_of(LINEAGE["aux_session_C"]["session_tokens"])


# Session D (`ses_fixture2`, title given as "probe D"): parent of `ses_fixture3` through its subagent call.
D_ID = "ses_fixture2"


D_MESSAGES = normalized(LINEAGE["nested_session_D"]["messages"])
D_TOTAL = total_of(SESSIONS[D_ID]["tokens"])


# Session A (`ses_fixture5`, title given as "probe A"): the P0.5 model and variant messages.
A_ID = "ses_fixture5"


A_MESSAGES = normalized(load("p0-5-model-variant-messages.json")["messages"])
A_TOTAL = total_of(SESSIONS[A_ID]["tokens"])
EVENTS = [opencode.normalize_event(event, families=FAMILIES) for event in EXCERPT["events"]]


def account(messages=C_MESSAGES, total=C_TOTAL, session_id=C_ID, **kwargs):
    kwargs.setdefault("lineage", [])  # launchers supply lineage; tests of its absence pass None
    return accounting.usage_from_messages(messages, total, session_id=session_id, **kwargs)


def state(messages=C_MESSAGES, total=C_TOTAL, session_id=C_ID, **extra):
    extra.setdefault("lineage", [])
    return {"session_id": session_id, "messages": messages, "tokens": total, **extra}


def event(aggregate, seq, event_id=None, version=1, kind="event", session=None):
    return {"schema": 1, "client": "opencode", "kind": kind, "type": "session.step.started",
            "event_id": event_id or f"evt_{aggregate[4:]}x{seq}", "session_id": session or aggregate,
            "durable_seq": seq, "durable_aggregate_id": aggregate, "durable_version": version}


def assert_fixed_error(case, function, *args, **kwargs):
    with case.assertRaises(AccountingError) as raised:
        function(*args, **kwargs)
    case.assertIn(str(raised.exception), accounting.REASONS)
    case.assertNotIn(MARKER, str(raised.exception))
    case.assertIsNone(raised.exception.__cause__)
    return str(raised.exception)


def idle(message_id, created_ms):
    return {"schema": 1, "client": "opencode", "kind": "message", "type": "idle", "message_id": message_id,
            "created_ms": created_ms, "completed_ms": None, "agent": None, "requested": None, "route_identity": None,
            "previous": None, "finish": None, "raw_finish": None, "outcome": "succeeded", "error_type": None,
            "tokens": None, "cost": None, "rate_limit": None, "tool_calls": [], "tool_calls_dropped": 0}


def spawn_call(child, status="completed", tool="subagent"):
    return {"tool": tool, "tool_class": "subagent" if tool == "subagent" else "builtin", "tool_call_id": "toolu_x",
            "executed": False, "status": status, "child_session_id": child, "spawn": None}


def synthetic(message_id, values, kind="assistant", alias="haiku", variant="default", cost=None, **extra):
    return {"schema": 1, "client": "opencode", "kind": "message", "type": kind, "message_id": message_id,
            "created_ms": 1794170000000, "completed_ms": 1794170000100 if kind == "assistant" else None,
            "agent": "build", "requested": {"provider_id": "claude-agent", "model_alias": alias, "variant": variant},
            "route_identity": None, "previous": None, "finish": "stop" if kind == "assistant" else None,
            "raw_finish": None, "outcome": None, "error_type": None, "tokens": dict(values), "cost": cost,
            "rate_limit": None, "tool_calls": [], "tool_calls_dropped": 0, **extra}


# Session `ses_fixture3` (agent "nester", child of session D): only its session total is recorded, so the test
# supplies one synthetic message carrying that total.
CHILD_ID = "ses_fixture3"


CHILD_TOTAL = total_of(SESSIONS[CHILD_ID]["tokens"])


CHILD = {"session_id": CHILD_ID, "tokens": CHILD_TOTAL,
         "messages": [synthetic("msg_child1", CHILD_TOTAL), idle("msg_child2", 1794170500000)]}


def tree_session(session_id, spawns=()):
    """A one-message session whose message spawns `spawns`; ids like `ses_x1` give messages like `msg_x1a`."""
    message = synthetic(f"msg_{session_id[4:]}a", tokens(1, 1, 0, 0, 0), cost=0.01,
                        tool_calls=[spawn_call(child) for child in spawns])
    return {"session_id": session_id, "tokens": tokens(1, 1, 0, 0, 0), "cost": 0.01, "messages": [message]}


def tree(root_spawns, children, funding="subscription", lineage=()):
    root = tree_session("ses_root", root_spawns)
    return accounting.usage_from_messages(root["messages"], root["tokens"], session_id="ses_root", funding=funding,
                                          session_cost=0.01, children=children,
                                          lineage=None if lineage is None else list(lineage))
