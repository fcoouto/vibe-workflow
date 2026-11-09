"""Read-only re-read of a stopped qualification run (tests/qualification/opencode_live.py) on its own isolated dir.

Restarts the server on the same directories with call budget 0 (the post recorder blocks every model request), re-reads
the P0.7 and managed sessions, probes one refused RPC and runs the accounting on the root with and without the
command-spawned child. Prints shapes, counts and fixed reasons only, through the harness's `Redactor`.

  python3 -B tests/qualification/opencode_inspect.py <isolated-dir-of-the-stopped-run>
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.qualification import opencode_live as live  # noqa: E402
from runtime import opencode_accounting as accounting  # noqa: E402
from runtime.adapters import opencode as adapter  # noqa: E402

base = Path(sys.argv[1])
hooks = [json.loads(line) for line in (base / "hooks.jsonl").read_text().splitlines()]
server = live.Server(base, 0)
server.start()
try:
    run = live.Run.__new__(live.Run)
    run.server = server
    sessions = []
    for record in hooks:
        if record.get("hook") == "model.request" and record["session"] not in sessions:
            sessions.append(record["session"])
    p07, managed, child = sessions[0], sessions[1], sessions[2]
    redact = live.Redactor(secrets=[server.password], paths=[str(base)])
    out = {}
    msgs = run.messages(p07)
    out["p07_types"] = [(m.get("type"), m.get("delivery"), m.get("outcome")) for m in msgs]
    out["p07_ids_equal_derived_user"] = [m.get("type") for m in msgs if m.get("id") == next(x["id"] for x in msgs if x["type"] == "user")]
    status, body, _ = server.rpc("attach", dict(session_id="ses_" + "0" * 32, attempt_id="qual-a1", authority="A" * 43,
                                                funding_pool="sub", model=live.ROUTE))
    out["rpc_refusal"] = dict(http=status, body=redact(body))
    root = run.messages(managed)
    out["managed_types"] = [(m.get("type"), m.get("outcome")) for m in root]
    _, record, _ = server.request("GET", "/api/session/%s" % managed)
    total = accounting.normalize_session(run.data(record))
    normalized = [adapter.normalize_message(m) for m in root]
    out["managed_normalized_kinds"] = [(n.get("kind"), n.get("type")) for n in normalized]
    out["session_total"] = total["tokens"]
    _, child_record, _ = server.request("GET", "/api/session/%s" % child)
    child_total = accounting.normalize_session(run.data(child_record))
    out["child"] = dict(parent_is_managed=run.data(child_record).get("parentID") == managed, tokens=child_total["tokens"],
                        types=[m.get("type") for m in run.messages(child)])
    out["subagent_tool_parts_in_root"] = sum(len(n.get("tool_calls") or []) for n in normalized)
    result = accounting.usage_from_messages(normalized, total["tokens"], session_id=managed, funding="subscription",
                                            complete=True, session_cost=total["cost"])
    out["root_only"] = dict(requests=[dict(kind=r["kind"], tokens=r["tokens"], cost=r.get("cost_usd_micros"))
                                      for r in result["usage"]["requests"]], unknown=result["unknown"], title=result["title"])
    state = dict(session_id=managed, messages=normalized, tokens=total["tokens"], cost=total["cost"], complete=True,
                 children=[dict(session_id=child, messages=[adapter.normalize_message(m) for m in run.messages(child)],
                                tokens=child_total["tokens"], cost=child_total["cost"], complete=True)])
    try:
        accounting.consistency_report(state)
        out["consistency_with_child"] = "accepted"
    except accounting.AccountingError as error:
        out["consistency_with_child"] = "refused: %s" % error
    print(json.dumps(redact(out), indent=1, default=str))
finally:
    server.stop()
